"""
메뉴얼 검색 + AI Q&A 데모 서버
실행: uvicorn server:app --reload --port 8000
"""

import json
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

load_dotenv()

app = FastAPI(title="메뉴얼 검색 시스템")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).parent
MANUALS_DIR = BASE_DIR / "manuals"
INDEX_PATH = MANUALS_DIR / "_index.json"


def load_index() -> list[dict]:
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_manual_content(file_path: str) -> str:
    full_path = MANUALS_DIR / file_path
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Manual not found")
    return full_path.read_text(encoding="utf-8")


# --- API Endpoints ---


@app.get("/")
async def root():
    return FileResponse(BASE_DIR / "frontend" / "index.html")


@app.get("/api/manuals")
async def list_manuals(
    homepage_large: Optional[str] = Query(None),
    homepage_medium: Optional[str] = Query(None),
    homepage_small: Optional[str] = Query(None),
    dept_large: Optional[str] = Query(None),
    dept_medium: Optional[str] = Query(None),
    dept_small: Optional[str] = Query(None),
):
    """필터 기반 메뉴얼 목록 조회"""
    manuals = load_index()
    results = []

    for m in manuals:
        match = True
        if homepage_large and m["homepage"]["대"] != homepage_large:
            match = False
        if homepage_medium and m["homepage"]["중"] != homepage_medium:
            match = False
        if homepage_small and m["homepage"]["소"] != homepage_small:
            match = False
        if dept_large and m["department"]["대"] != dept_large:
            match = False
        if dept_medium and m["department"]["중"] != dept_medium:
            match = False
        if dept_small and m["department"]["소"] != dept_small:
            match = False
        if match:
            results.append(m)

    return {"count": len(results), "manuals": results}


@app.get("/api/manuals/{manual_id}")
async def get_manual(manual_id: str):
    """메뉴얼 상세 조회 (내용 포함)"""
    manuals = load_index()
    for m in manuals:
        if m["id"] == manual_id:
            content = load_manual_content(m["file"])
            return {**m, "content": content}
    raise HTTPException(status_code=404, detail="Manual not found")


@app.get("/api/filters")
async def get_filters():
    """사용 가능한 필터 값 목록"""
    manuals = load_index()
    homepage = {"대": set(), "중": set(), "소": set()}
    department = {"대": set(), "중": set(), "소": set()}

    for m in manuals:
        for level in ["대", "중", "소"]:
            homepage[level].add(m["homepage"][level])
            department[level].add(m["department"][level])

    return {
        "homepage": {k: sorted(v) for k, v in homepage.items()},
        "department": {k: sorted(v) for k, v in department.items()},
    }


class SearchRequest(BaseModel):
    query: str


@app.post("/api/search")
async def search_manuals(req: SearchRequest):
    """키워드 기반 메뉴얼 검색"""
    manuals = load_index()
    query = req.query.lower()
    results = []

    for m in manuals:
        score = 0
        searchable = f"{m['title']} {m['detail']} {' '.join(m['tags'])}".lower()
        for word in query.split():
            if word in searchable:
                score += 1
            # 태그 정확 매칭은 가중치
            if word in [t.lower() for t in m["tags"]]:
                score += 2

        if score > 0:
            content = load_manual_content(m["file"])
            content_lower = content.lower()
            for word in query.split():
                if word in content_lower:
                    score += 1

            results.append({**m, "score": score})

    results.sort(key=lambda x: x["score"], reverse=True)
    return {"count": len(results), "manuals": results}


class QARequest(BaseModel):
    question: str
    manual_ids: list[str] = []
    api_key: str = ""


@app.post("/api/qa")
async def qa(req: QARequest):
    """메뉴얼 기반 AI Q&A"""
    api_key = req.api_key or os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="OpenAI API 키가 필요합니다. .env 파일에 OPENAI_API_KEY를 설정하거나 요청에 api_key를 포함해주세요.",
        )

    # 관련 메뉴얼 내용 수집
    manuals = load_index()
    context_parts = []

    if req.manual_ids:
        # 특정 메뉴얼 지정
        for m in manuals:
            if m["id"] in req.manual_ids:
                content = load_manual_content(m["file"])
                context_parts.append(f"## {m['title']}\n{content}")
    else:
        # 질문 기반 자동 검색
        search_result = await search_manuals(SearchRequest(query=req.question))
        for m in search_result["manuals"][:5]:
            content = load_manual_content(m["file"])
            context_parts.append(f"## {m['title']}\n{content}")

    if not context_parts:
        return {
            "answer": "관련 메뉴얼을 찾지 못했습니다. 다른 질문을 시도해주세요.",
            "sources": [],
        }

    context = "\n\n---\n\n".join(context_parts)

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "당신은 휴텍씨 메뉴얼 검색 어시스턴트입니다. "
                        "아래 제공된 메뉴얼 내용을 기반으로 질문에 답변하세요. "
                        "메뉴얼에 없는 내용은 추측하지 말고, 해당 정보가 메뉴얼에 없다고 알려주세요. "
                        "답변 시 출처(메뉴얼 제목)를 명시해주세요."
                    ),
                },
                {
                    "role": "user",
                    "content": f"## 참고 메뉴얼\n\n{context}\n\n---\n\n## 질문\n{req.question}",
                },
            ],
            temperature=0.3,
            max_tokens=1500,
        )

        return {
            "answer": response.choices[0].message.content,
            "sources": [
                m["title"]
                for m in manuals
                if m["id"] in req.manual_ids
                or (
                    not req.manual_ids
                    and any(
                        word in m["title"].lower()
                        for word in req.question.lower().split()
                    )
                )
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 응답 생성 실패: {str(e)}")
