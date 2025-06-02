# -*- coding: utf-8 -*-
import os
import openai
import requests
from datetime import datetime, timedelta
import re
from dateparser.search import search_dates
from fastapi import FastAPI, Request
from pydantic import BaseModel
import urllib.parse

app = FastAPI()
client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

memory_store = {}

def init_context():
    return {
        "destination": None,
        "departure_date": None,
        "return_date": None,
        "duration": None,
        "adults_number": None,
        "children_number": 0,
        "no_rooms": 1,
        "flight_asked": False,
        "hotel_asked": False,
        "hotel_filter": None,
        "food_asked": False,
        "food_filter": None
    }

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

def extract_location_by_regex(text):
    city_keywords = [
        # 🇯🇵 일본
        "오사카", "도쿄", "후쿠오카", "교토", "삿포로", "나고야", "나라", "요코하마",
        # 🇰🇷 한국
        "서울", "부산", "제주", "인천", "대구", "광주", "대전", "수원",
        # 🇺🇸 미국
        "뉴욕", "로스앤젤레스", "샌프란시스코", "라스베가스", "시카고",
        # 🇫🇷 프랑스
        "파리", "리옹", "마르세유",
        # 🇮🇹 이탈리아
        "로마", "밀라노", "베네치아", "피렌체",
        # 🇪🇸 스페인
        "바르셀로나", "마드리드", "세비야",
        # 🇬🇧 영국
        "런던", "에딘버러", "맨체스터",
        # 🇹🇭 태국
        "방콕", "푸켓", "치앙마이",
        # 기타 주요 여행지
        "하와이", "발리", "싱가포르", "홍콩", "마카오", "두바이"
    ]
    for city in city_keywords:
        if city in text:
            return city
    return None

def extract_location_keyword_gpt(user_input):
    prompt = """
    다음 문장에서 여행 목적지나 도시 이름을 한 단어로 추출해줘.
    예: '오사카 맛집 추천해줘' → '오사카'
    예: '서울 숙소 예약하고 싶어' → '서울'
    예: '도쿄 여행가고 싶어' → '도쿄'
    예: '후쿠오카 호텔 알려줘' → '후쿠오카'
    예: '일본에서 놀고 싶어' → '일본'
    만약 추출할 수 없다면 '없음'으로 답해줘.
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": prompt}, {"role": "user", "content": user_input}]
        )
        extracted = response.choices[0].message.content.strip()
        if not extracted or extracted.lower() in ["없음", "없다", "null", "none"]:
            return extract_location_by_regex(user_input)
        return extracted
    except Exception as e:
        print("❌ GPT 위치 키워드 추출 실패:", str(e))
        return extract_location_by_regex(user_input)

def extract_hotel_filter_keywords_gpt(user_input):
    prompt = "다음 문장에서 호텔 특성 키워드를 모두 추출해줘. 쉼표로 구분해서 한글 키워드만. 예: '수영장 있는 가성비 좋은 호텔' → 수영장, 가성비"
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": prompt}, {"role": "user", "content": user_input}]
    )
    keywords = response.choices[0].message.content.strip()
    return [kw.strip() for kw in keywords.split(",")]

def update_context(user_input, conversation_context):
    # 💡 목적지 키워드는 요청 종류와 무관하게 항상 추출 시도, 단 이미 있으면 중복 호출 방지
    if not conversation_context["destination"]:
        new_dest = extract_location_keyword_gpt(user_input)
        if new_dest and new_dest.lower() not in ["없음", "none", "null"]:
            conversation_context["destination"] = new_dest

    if any(k in user_input for k in ["숙소", "호텔", "잠잘 곳", "묵을 곳", "자고싶어"]):
        conversation_context["hotel_asked"] = True
        if not conversation_context["hotel_filter"]:
            conversation_context["hotel_filter"] = extract_hotel_filter_keywords_gpt(user_input)

    if any(k in user_input for k in ["맛집", "음식", "카페", "배고파", "먹을 곳"]):
        conversation_context["food_asked"] = True
        filter_keywords = ["감성", "인스타", "해변", "해변 근처", "분위기 좋은", "인기 많은", "저렴한"]
        for keyword in filter_keywords:
            if keyword in user_input:
                conversation_context["food_filter"] = keyword
                break

    if not conversation_context["departure_date"] or not conversation_context["return_date"]:
        checkin, checkout = extract_dates_from_message(user_input)
        if checkin and checkout:
            conversation_context["departure_date"] = checkin
            conversation_context["return_date"] = checkout
            conversation_context["duration"] = (datetime.strptime(checkout, "%Y-%m-%d") - datetime.strptime(checkin, "%Y-%m-%d")).days

    # 성인 수 인식
    adult_match = re.search(r'성인\s*([0-9]+|[일이삼사오육칠팔구십]+)', user_input)
    if adult_match:
        conversation_context["adults_number"] = korean_number_to_int(adult_match.group(1))

    # 어린이 수 인식
    child_match = re.search(r'어린이\s*([0-9]+|[일이삼사오육칠팔구십]+)', user_input)
    if child_match:
        conversation_context["children_number"] = korean_number_to_int(child_match.group(1))

    # GPT 응답을 기반으로 호텔/맛집 요청 여부를 보완
    if conversation_context["destination"] and not conversation_context["hotel_asked"]:
        if any(k in user_input for k in ["숙소", "호텔"]):
            conversation_context["hotel_asked"] = True
            if not conversation_context["hotel_filter"]:
                conversation_context["hotel_filter"] = extract_hotel_filter_keywords_gpt(user_input)

    if conversation_context["destination"] and not conversation_context["food_asked"]:
        if any(k in user_input for k in ["맛집", "음식", "카페"]):
            conversation_context["food_asked"] = True
            filter_keywords = ["감성", "인스타", "해변", "해변 근처", "분위기 좋은", "인기 많은", "저렴한"]
            for keyword in filter_keywords:
                if keyword in user_input:
                    conversation_context["food_filter"] = keyword
                    break

def search_hotels_by_dest_id(dest_id, checkin, checkout, filter_keywords=None, context=None):
    url = "https://booking-com.p.rapidapi.com/v1/hotels/search"
    headers = {
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": "booking-com.p.rapidapi.com"
    }
    querystring = {
        "checkin_date": checkin,
        "checkout_date": checkout,
        "dest_id": dest_id,
        "dest_type": "city",
        "adults_number": context.get("adults_number", 2),
        "units": "metric",
        "order_by": "popularity",
        "locale": "ko",
        "currency": "KRW",
        "filter_by_currency": "KRW",
        "room_number": context.get("no_rooms", 1),
        "page_number": "0"
    }
    categories_map = {
        "럭셔리": ["class::5", "class::4"],
        "저렴한": ["price::1"],
        "가성비": ["price::1", "review_score::8"],
        "수영장": ["facility::11"],
        "조식": ["mealplan::1"],
        "조식포함": ["mealplan::1"],
        "반려동물": ["facility::5"]
    }
    categories = []
    if filter_keywords:
        for kw in filter_keywords:
            if kw in categories_map:
                categories.extend(categories_map[kw])
    if categories:
        querystring["categories_filter_ids"] = ",".join(categories)

    response = requests.get(url, headers=headers, params=querystring)
    if response.status_code != 200:
        print("❌ 호텔 검색 API 오류:", response.text)
        return []
    print("📍 Booking 검색 응답 코드:", response.status_code)
    data = response.json()
    hotels = []
    for hotel in data.get("result", [])[:5]:
        hotels.append({
            "name": hotel.get("hotel_name"),
            "price": int(hotel.get("min_total_price", 0)) if hotel.get("min_total_price") else 0,
            "rating": hotel.get("review_score"),
            "url": (
                f"https://www.booking.com/searchresults.ko.html?"
                f"ss={hotel.get('hotel_name')}&"
                f"checkin_year={checkin[:4]}&checkin_month={int(checkin[5:7])}&checkin_monthday={int(checkin[8:10])}&"
                f"checkout_year={checkout[:4]}&checkout_month={int(checkout[5:7])}&checkout_monthday={int(checkout[8:10])}&"
                f"group_adults={context.get('adults_number',2)}&group_children={context.get('children_number',0)}&no_rooms={context.get('no_rooms',1)}"
            )
        })
    return hotels

def recommend_food_places(destination, context=None):
    if not destination:
        return []
    query = destination + " 맛집"
    if context.get("food_filter"):
        query = f"{destination} {context['food_filter']} 맛집"
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {
        "query": query,
        "language": "ko",
        "region": context.get("destination", ""),
        "key": GOOGLE_API_KEY
    }
    response = requests.get(url, params=params)
    results = response.json().get("results", [])
    print("🍴 Google 결과:", results)
    food_list = []
    for place in results[:5]:
        name = place.get("name")
        rating = place.get("rating", "-")
        address = place.get("formatted_address", "주소 정보 없음")
        map_url = f"https://www.google.com/maps/search/?api=1&query={name.replace(' ', '+')}"
        food_list.append({
            "name": name,
            "rating": rating,
            "address": address,
            "url": map_url
        })
    return food_list

def get_dest_id_from_booking(query):
    url = "https://booking-com.p.rapidapi.com/v1/hotels/locations"
    headers = {
        "X-RapidAPI-Key": RAPIDAPI_KEY,
        "X-RapidAPI-Host": "booking-com.p.rapidapi.com"
    }
    print("📍 Booking 대상:", query)
    params = {"name": query, "locale": "ko"}
    response = requests.get(url, headers=headers, params=params)
    try:
        results = response.json()
        if isinstance(results, list) and results:
            for item in results:
                if item.get("dest_type") == "city":
                    return item.get("name"), item.get("dest_id")
    except:
        print("❌ dest_id 조회 실패:", response.text)
    return None, None

@app.post("/chat/reset")
async def reset_context(req: Request):
    data = await req.json()
    user_id = data.get("user_id", "default")
    chat_id = data.get("chat_id", "default")
    context_key = f"{user_id}_{chat_id}"
    memory_store[context_key] = {
        "destination": None,
        "departure_date": None,
        "return_date": None,
        "duration": None,
        "adults_number": None,
        "children_number": 0,
        "no_rooms": 1,
        "flight_asked": False,
        "hotel_asked": False,
        "hotel_filter": None,
        "food_asked": False,
        "food_filter": None
    }
    return {"status": "reset"}

@app.post("/chat")
async def chat(req: Request):
    data = await req.json()
    user_input = data.get("user_input", "")
    user_id = data.get("user_id", "default")
    chat_id = data.get("chat_id", "default")
    context_key = f"{user_id}_{chat_id}"

    if context_key not in memory_store:
        memory_store[context_key] = init_context()

    context = memory_store[context_key]
    update_context(user_input, context)

    def memory_text():
        parts = []
        if context["destination"]:
            parts.append(f"여행지는 {context['destination']}")
        if context["departure_date"]:
            parts.append(f"출발일은 {context['departure_date']}")
        if context["duration"]:
            parts.append(f"{context['duration']}일 일정")
        if context["adults_number"]:
            parts.append(f"성인 {context['adults_number']}명")
        if context["children_number"]:
            parts.append(f"어린이 {context['children_number']}명")
        return ", ".join(parts) if parts else "없음"

    prompt = f"""
    너는 친절한 여행 챗봇이야. 사용자의 대화를 이어서 여행 계획을 도와줘.
    아래는 지금까지 사용자 정보야: {memory_text()}
    목적지, 출발일, 여행 기간, 성인수/어린이수 정보 중 빠진 것이 있을 때만 자연스럽게 물어봐줘.
    이미 받은 정보는 다시 묻지 말고, 대화를 이어서 부드럽게 안내해줘.
    항상 간결하고 부드럽게 1~2문장으로 대답해줘.
    """

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_input}
        ]
    )

    hotel_recommendations = []
    if context["hotel_asked"] and context["destination"]:
        dest_name, dest_id = get_dest_id_from_booking(context["destination"])
        if dest_id:
            hotel_recommendations = search_hotels_by_dest_id(
                dest_id,
                context["departure_date"],
                context["return_date"],
                context.get("hotel_filter") or [],
                context=context
            )

    food_recommendations = []
    if context["food_asked"] and context["destination"]:
        food_recommendations = recommend_food_places(context["destination"], context=context)

    # 호텔/맛집 요청 여부 초기화
    context["hotel_asked"] = False
    context["food_asked"] = False

    response_data = {
        "context": context
    }
    response_data["recommendation"] = response.choices[0].message.content.strip()
    if hotel_recommendations:
        response_data["hotels"] = hotel_recommendations
    if food_recommendations:
        response_data["foods"] = food_recommendations

    return response_data

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=10000, reload=True)