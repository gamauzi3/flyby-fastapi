# -*- coding: utf-8 -*-
import openai
import requests
from datetime import datetime, timedelta
import re
from dateparser.search import search_dates
from fastapi import FastAPI, Request
from pydantic import BaseModel
import urllib.parse

app = FastAPI()
client = openai.OpenAI(api_key="REMOVED_KEY-3_bJtv46F7XD_YKbNJPFMnlJvw3EWtxyz6OQS52tW0U1hOC4HM-6u4LilxQjE_Qa6_8B6FyH3WT3BlbkFJPq-KPoI7tEVFtn0Uqtdv0Dtj8HIrKzeOxzKaRw9X5XU-QM3f-kgOhEzbGjEPGmZlr-RTfz2S4A")  # 실제 key는 환경 변수 등으로 설정 필요
RAPIDAPI_KEY = "73fdc45c6fmsh02f06e0f16adb19p18c683jsnd8576332ca19"
GOOGLE_API_KEY = "AIzaSyBr3cf07Kw0vN8Ydf0JXnps7fRmyauN9Sw"

conversation_context = {
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

def extract_location_keyword_gpt(user_input):
    prompt = "다음 문장에서 숙소 위치 키워드만 뽑아줘. 예: '난바역 근처 호텔' → '난바역'"
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": prompt}, {"role": "user", "content": user_input}]
    )
    return response.choices[0].message.content.strip()

def extract_hotel_filter_keywords_gpt(user_input):
    prompt = "다음 문장에서 호텔 특성 키워드를 모두 추출해줘. 쉼표로 구분해서 한글 키워드만. 예: '수영장 있는 가성비 좋은 호텔' → 수영장, 가성비"
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": prompt}, {"role": "user", "content": user_input}]
    )
    keywords = response.choices[0].message.content.strip()
    return [kw.strip() for kw in keywords.split(",")]

def update_context(user_input):
    if "호텔" in user_input or "숙소" in user_input:
        conversation_context["hotel_asked"] = True
    if any(k in user_input for k in ["맛집", "음식", "카페"]):
        conversation_context["food_asked"] = True
        filter_keywords = ["감성", "인스타", "해변", "해변 근처", "분위기 좋은", "인기 많은", "저렴한"]
        for keyword in filter_keywords:
            if keyword in user_input:
                conversation_context["food_filter"] = keyword
                break
    if not conversation_context["destination"]:
        conversation_context["destination"] = extract_location_keyword_gpt(user_input)
    if not conversation_context["hotel_filter"]:
        conversation_context["hotel_filter"] = extract_hotel_filter_keywords_gpt(user_input)
    if not conversation_context["departure_date"] or not conversation_context["return_date"]:
        checkin, checkout = extract_dates_from_message(user_input)
        if checkin and checkout:
            conversation_context["departure_date"] = checkin
            conversation_context["return_date"] = checkout
            conversation_context["duration"] = (datetime.strptime(checkin, "%Y-%m-%d") - datetime.strptime(checkout, "%Y-%m-%d")).days
    # Removed all input() calls related to adults_number and children_number

def search_hotels_by_dest_id(dest_id, checkin, checkout, filter_keywords=None):
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
        "adults_number": conversation_context.get("adults_number", 2),
        "units": "metric",
        "order_by": "popularity",
        "locale": "ko",
        "currency": "KRW",
        "filter_by_currency": "KRW",
        "room_number": conversation_context.get("no_rooms", 1),
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
    data = response.json()
    hotels = []
    for hotel in data.get("result", [])[:5]:
        hotels.append({
            "name": hotel.get("hotel_name"),
            "price": hotel.get("min_total_price"),
            "rating": hotel.get("review_score"),
            "url": (
                f"https://www.booking.com/searchresults.ko.html?"
                f"ss={hotel.get('hotel_name')}&"
                f"checkin_year={checkin[:4]}&checkin_month={int(checkin[5:7])}&checkin_monthday={int(checkin[8:10])}&"
                f"checkout_year={checkout[:4]}&checkout_month={int(checkout[5:7])}&checkout_monthday={int(checkout[8:10])}&"
                f"group_adults={conversation_context.get('adults_number',2)}&group_children={conversation_context.get('children_number',0)}&no_rooms={conversation_context.get('no_rooms',1)}"
            )
        })
    return hotels

def recommend_food_places(destination):
    if not destination:
        return ["❗ 도시 정보가 없어요. 맛집을 추천하려면 도시를 먼저 알려주세요."]
    query = destination + " 맛집"
    if conversation_context.get("food_filter"):
        query = f"{destination} {conversation_context['food_filter']} 맛집"
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {
        "query": query,
        "language": "ko",
        "key": GOOGLE_API_KEY
    }
    response = requests.get(url, params=params)
    results = response.json().get("results", [])
    food_list = []
    for place in results[:5]:
        name = place.get("name")
        rating = place.get("rating", "-")
        address = place.get("formatted_address", "주소 정보 없음")
        map_url = f"https://www.google.com/maps/search/?api=1&query={name.replace(' ', '+')}"
        summary = f"🍽 {name} (⭐ {rating})\n📍 {address}\n🔗 {map_url}"
        food_list.append(summary)
    return food_list or ["⚠️ 맛집 정보를 불러올 수 없어요."]

@app.post("/chat")
async def chat(req: Request):
    data = await req.json()
    user_input = data.get("user_input", "")
    update_context(user_input)

    def memory_text():
        parts = []
        if conversation_context["destination"]:
            parts.append(f"여행지는 {conversation_context['destination']}")
        if conversation_context["departure_date"]:
            parts.append(f"출발일은 {conversation_context['departure_date']}")
        if conversation_context["duration"]:
            parts.append(f"{conversation_context['duration']}일 일정")
        if conversation_context["adults_number"]:
            parts.append(f"성인 {conversation_context['adults_number']}명")
        if conversation_context["children_number"]:
            parts.append(f"어린이 {conversation_context['children_number']}명")
        return ", ".join(parts) if parts else "없음"

    prompt = f"""
    너는 친절한 여행 챗봇이야. 사용자의 대화를 보고 숙소나 항공, 맛집 추천을 해줘.
    아래는 지금까지 사용자 정보야: {memory_text()}
    목적지, 출발일, 여행 기간, 성인수/어린이수 정보 중 빠진 것이 있을 때만 자연스럽게 물어봐줘.
    성인수, 어린이 수 정보가 없으면 자연스럽게 물어봐줘.
    이미 받은 정보는 다시 묻지 말고, 대화를 이어서 여행 계획을 제안해줘.
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
    if conversation_context["hotel_asked"]:
        hotel_recommendations = search_hotels_by_dest_id("270442", conversation_context["departure_date"], conversation_context["return_date"], conversation_context.get("hotel_filter", []))

    food_recommendations = []
    if conversation_context["food_asked"]:
        food_recommendations = recommend_food_places(conversation_context["destination"])

    return {
        "recommendation": response.choices[0].message.content.strip(),
        "context": conversation_context,
        "hotel_recommendations": hotel_recommendations,
        "food_recommendations": food_recommendations
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=10000, reload=True)