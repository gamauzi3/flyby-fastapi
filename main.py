from fastapi import FastAPI
from pydantic import BaseModel
import openai
import os

app = FastAPI()
client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# 임시 사용자별 대화 상태 저장소
conversation_contexts = {}

class ChatRequest(BaseModel):
    user_id: str
    user_input: str

def update_context(user_id: str, user_input: str):
    if user_id not in conversation_contexts:
        conversation_contexts[user_id] = {
            "destination": None,
            "duration": None,
            "adults_number": None
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

    # 인원수 추출
    if context["adults_number"] is None:
        prompt = f"\"{user_input}\" 문장에서 성인 수를 숫자로 추출해줘. 없다면 'null'."
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}]
        )
        adults = resp.choices[0].message.content.strip()
        if adults.lower() != "null":
            try:
                context["adults_number"] = int(adults)
            except:
                pass

    return context

@app.post("/chat")
async def chat(req: ChatRequest):
    context = update_context(req.user_id, req.user_input)

    # 핵심 정보가 모두 모였는지 확인
    if all([context["destination"], context["duration"], context["adults_number"]]):
        prompt = f"""
        너는 친절한 여행 챗봇이야. 아래 사용자 정보를 기반으로 여행 일정을 추천해줘.
        - 목적지: {context["destination"]}
        - 일정: {context["duration"]}일
        - 성인 수: {context["adults_number"]}명

        목적지에 어울리는 숙소, 맛집, 활동을 2~3가지 제안해줘. 항상 간결하고 부드럽게 1~2문장으로 대답해줘.
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
        if not context["destination"]:
            needed.append("여행 목적지")
        if not context["duration"]:
            needed.append("일정 (며칠)")
        if not context["adults_number"]:
            needed.append("성인 수")

        return {
            "recommendation": f"{', '.join(needed)} 정보를 알려주세요!",
            "context": context
        }