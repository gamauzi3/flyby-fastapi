from fastapi import FastAPI
from pydantic import BaseModel
import openai
import os
from datetime import datetime, timedelta
import re
from dateparser.search import search_dates

app = FastAPI()
client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# 임시 사용자별 대화 상태 저장소
conversation_contexts = {}

class ChatRequest(BaseModel):
    user_id: str
    user_input: str

def korean_number_to_int(text):
    mapping = {'일':1, '이':2, '삼':3, '사':4, '오':5, '육':6, '칠':7, '팔':8, '구':9, '십':10}
    if text.isdigit():
        return int(text)
    result = 0
    if '십' in text:
        parts = text.split('십')
        if parts[0] == '':
            result += 10
        else:
            result += mapping[parts[0]] * 10
        if len(parts) > 1 and parts[1] in mapping:
            result += mapping[parts[1]]
    else:
        if text in mapping:
            result += mapping[text]
    return result

def extract_dates_from_message(message):
    manual_match = re.search(r'(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일', message)
    if manual_match:
        year = int(manual_match.group(1))
        month = int(manual_match.group(2))
        day = int(manual_match.group(3))
        departure = datetime(year, month, day)
    else:
        manual_match = re.search(r'(\d{1,2})월\s*(\d{1,2})일', message)
        if manual_match:
            now = datetime.now()
            month = int(manual_match.group(1))
            day = int(manual_match.group(2))
            year = now.year
            if month < now.month:
                year += 1
            departure = datetime(year, month, day)
        else:
            date_match = search_dates(message, languages=["ko"])
            if date_match:
                departure = date_match[0][1]
            else:
                departure = None

    nights = None
    stay_match = re.search(r'([0-9]+|[일이삼사오육칠팔구십]+)\s*박\s*([0-9]+|[일이삼사오육칠팔구십]+)\s*일', message)
    if stay_match:
        nights = korean_number_to_int(stay_match.group(2))
    else:
        duration_match = re.search(r'([0-9]+|[일이삼사오육칠팔구십]+)\s*일', message)
        if duration_match:
            nights = korean_number_to_int(duration_match.group(1))
    if departure and nights:
        checkin = departure.date()
        checkout = (departure + timedelta(days=nights)).date()
        return str(checkin), str(checkout)
    return None, None

def update_context(user_id: str, user_input: str):
    if user_id not in conversation_contexts:
        conversation_contexts[user_id] = {
            "destination": None,
            "duration": None,
            "adults_number": None,
            "departure_date": None,
            "return_date": None,
            "children_number": 0,
            "no_rooms": 1,
            "flight_asked": False,
            "hotel_asked": False,
            "hotel_filter": None,
            "food_asked": False,
            "food_filter": None
        }

    context = conversation_contexts[user_id]

    # 목적지 추출
    if context["destination"] is None:
        prompt = f"\"{user_input}\" 문장에서 여행 목적지를 간단히 추출해줘. 없다면 'null'이라고 말해."
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}]
        )
        location = resp.choices[0].message.content.strip()
        if location.lower() != "null":
            context["destination"] = location

    # 일정 추출
    if context["duration"] is None:
        prompt = f"\"{user_input}\" 문장에서 여행 일정을 일수로 추출해줘. 없다면 'null'."
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}]
        )
        duration = resp.choices[0].message.content.strip()
        if duration.lower() != "null":
            try:
                context["duration"] = int(duration)
            except:
                pass

    # 성인 수 추출
    if context["adults_number"] is None:
        direct_number = re.findall(r"\d+", user_input)
        if direct_number:
            number = int(direct_number[0])
            if number >= 1:
                context["adults_number"] = number
        else:
            prompt = """
            아래 문장에서 여행에 참여하는 성인 수를 숫자 하나로 추출해줘. 예: '4명', '셋', '둘이서 여행', '성인 2명' → 2
            숫자가 없다면 'null'이라고 답해.
            """
            resp = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_input}
                ]
            )
            adults = resp.choices[0].message.content.strip()
            if adults.isdigit():
                number = int(adults)
                if number >= 1:
                    context["adults_number"] = number

    # 음식 필터 감지
    if any(k in user_input for k in ["맛집", "음식", "카페"]):
        context["food_asked"] = True
        filter_keywords = ["감성", "인스타", "해변", "해변 근처", "분위기 좋은", "인기 많은", "저렴한"]
        for keyword in filter_keywords:
            if keyword in user_input:
                context["food_filter"] = keyword
                break

    # 날짜 추출
    if context["departure_date"] is None or context["return_date"] is None:
        checkin, checkout = extract_dates_from_message(user_input)
        if checkin and checkout:
            context["departure_date"] = checkin
            context["return_date"] = checkout

    # 날짜로 duration 계산
    if context["departure_date"] and context["return_date"] and context["duration"] is None:
        try:
            d1 = datetime.strptime(context["departure_date"], "%Y-%m-%d")
            d2 = datetime.strptime(context["return_date"], "%Y-%m-%d")
            context["duration"] = (d2 - d1).days
        except:
            pass

    return context

@app.post("/chat")
async def chat(req: ChatRequest):
    context = update_context(req.user_id, req.user_input)

    if context["food_asked"] and context["destination"]:
        return {
            "recommendation": f"{context['destination']}의 맛집을 곧 추천해드릴게요!",
            "context": context
        }
    elif context["hotel_asked"] and context["destination"]:
        return {
            "recommendation": f"{context['destination']}의 숙소도 찾아볼게요!",
            "context": context
        }

    # 핵심 정보가 모두 모였는지 확인
    if (
        context["destination"] is not None and
        context["duration"] is not None and
        context["adults_number"] is not None and
        context["adults_number"] >= 1
    ):
        prompt = f"""
        너는 친절한 여행 챗봇이야. 아래 사용자 정보를 기반으로 여행 일정을 추천해줘.
        - 목적지: {context["destination"]}
        - 일정: {context["duration"]}일
        - 성인 수: {context["adults_number"]}명

        목적지에 어울리는 숙소, 맛집, 활동을 2~3가지 제안해줘. 항상 간결하고 부드럽게 1~2문장으로 대답해줘.
        이미 입력된 정보는 다시 묻지 마.
        """
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": prompt}, {"role": "user", "content": "추천해줘"}]
        )
        return {
            "recommendation": resp.choices[0].message.content.strip(),
            "context": context
        }
    else:
        # 아직 정보가 부족한 경우 사용자에게 질문 유도
        needed = []
        if context.get("destination") is None:
            needed.append("여행 목적지")
        if context.get("duration") is None:
            needed.append("일정 (며칠)")
        if context.get("adults_number") is None:
            needed.append("성인 수")

        return {
            "recommendation": f"{', '.join(needed)} 정보를 알려주세요!",
            "context": context
        }