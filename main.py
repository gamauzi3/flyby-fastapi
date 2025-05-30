from fastapi import FastAPI
from pydantic import BaseModel
import openai
import os

app = FastAPI()

client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

class TravelRequest(BaseModel):
    destination: str
    duration: int
    adults: int

@app.post("/recommend")
async def recommend(data: TravelRequest):
    prompt = f"{data.destination}으로 {data.adults}명이 {data.duration}일 동안 여행을 가요. 추천 일정을 알려줘."
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}]
    )
    return {"recommendation": response.choices[0].message.content}