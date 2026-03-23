"""
메뉴얼 검색 + AI Q&A 데모 서버
실행: uvicorn server:app --reload --port 8000
"""

import json
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
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
COLORS_PATH = MANUALS_DIR / "_colors.json"


def load_colors() -> dict:
    if COLORS_PATH.exists():
        with open(COLORS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_index() -> list[dict]:
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_index(data: list[dict]):
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_manual_content(file_path: str) -> str:
    full_path = MANUALS_DIR / file_path
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Manual not found")
    return full_path.read_text(encoding="utf-8")


def build_searchable_text(m: dict) -> str:
    """메뉴얼의 모든 메타데이터를 검색 가능한 텍스트로 결합"""
    parts = [m.get("title", ""), " ".join(m.get("tags", []))]
    for axis in ["A분야", "B급수"]:
        for level in ["대", "중", "소"]:
            val = m.get(axis, {}).get(level, "")
            if val and val != "-":
                parts.append(val)
    for level in ["대", "세부"]:
        val = m.get("C홈페이지", {}).get(level, "")
        if val and val != "-":
            parts.append(val)
    for level in ["대", "중"]:
        val = m.get("D부서", {}).get(level, "")
        if val and val != "-":
            parts.append(val)
    e = m.get("E등급", "")
    if e and e != "-":
        parts.append(e)
    return " ".join(parts).lower()


# --- API ---


@app.get("/api/colors")
async def get_colors():
    """색상 매핑 반환"""
    return load_colors()


@app.get("/health")
async def health():
    """UptimeRobot 핑용 헬스체크"""
    return {"status": "ok"}


@app.get("/")
async def root():
    return FileResponse(BASE_DIR / "frontend" / "index.html")


@app.get("/api/manuals")
async def list_manuals(
    a_large: Optional[str] = Query(None),
    a_medium: Optional[str] = Query(None),
    b_large: Optional[str] = Query(None),
    b_medium: Optional[str] = Query(None),
    b_small: Optional[str] = Query(None),
    c_large: Optional[str] = Query(None),
    d_large: Optional[str] = Query(None),
    e_grade: Optional[str] = Query(None),
):
    """5축 필터 기반 메뉴얼 목록 조회"""
    manuals = load_index()
    results = []

    for m in manuals:
        match = True
        if a_large and m["A분야"]["대"] != a_large:
            match = False
        if a_medium and m["A분야"]["중"] != a_medium:
            match = False
        if b_large and m["B급수"]["대"] != b_large:
            match = False
        if b_medium and m["B급수"]["중"] != b_medium:
            match = False
        if b_small and m["B급수"]["소"] != b_small:
            match = False
        if c_large and m["C홈페이지"]["대"] != c_large:
            match = False
        if d_large and m["D부서"]["대"] != d_large:
            match = False
        if e_grade and m.get("E등급", "-") != e_grade:
            match = False
        if match:
            results.append(m)

    return {"count": len(results), "manuals": results}


@app.get("/api/manuals/{manual_id}")
async def get_manual(manual_id: str):
    manuals = load_index()
    for m in manuals:
        if m["id"] == manual_id:
            content = load_manual_content(m["file"])
            return {**m, "content": content}
    raise HTTPException(status_code=404, detail="Manual not found")


@app.get("/api/filters")
async def get_filters():
    """사용 가능한 필터 값 목록 (5축)"""
    manuals = load_index()
    filters = {
        "A분야": {"대": set(), "중": set(), "소": set()},
        "B급수": {"대": set(), "중": set(), "소": set()},
        "C홈페이지": {"대": set(), "세부": set()},
        "D부서": {"대": set(), "중": set()},
        "E등급": set(),
    }

    for m in manuals:
        for level in ["대", "중", "소"]:
            for axis in ["A분야", "B급수"]:
                val = m.get(axis, {}).get(level, "")
                if val and val != "-":
                    filters[axis][level].add(val)
        for level in ["대", "세부"]:
            val = m.get("C홈페이지", {}).get(level, "")
            if val and val != "-":
                filters["C홈페이지"][level].add(val)
        for level in ["대", "중"]:
            val = m.get("D부서", {}).get(level, "")
            if val and val != "-":
                filters["D부서"][level].add(val)
        val = m.get("E등급", "")
        if val and val != "-":
            filters["E등급"].add(val)

    result = {}
    for axis, levels in filters.items():
        if isinstance(levels, set):
            result[axis] = sorted(levels)
        else:
            result[axis] = {k: sorted(v) for k, v in levels.items()}
    return result


class SearchRequest(BaseModel):
    query: str


@app.post("/api/search")
async def search_manuals(req: SearchRequest):
    """전체 5축 + 태그 + 본문 검색 (AND 우선, 결과 없으면 OR 폴백)"""
    manuals = load_index()
    query = req.query.lower()
    words = query.split()

    def score_manual(m, required_all: bool) -> Optional[dict]:
        searchable = build_searchable_text(m)
        content = load_manual_content(m["file"])
        full_text = f"{searchable} {content.lower()}"

        if required_all and not all(w in full_text for w in words):
            return None

        score = 0
        for word in words:
            if word in searchable:
                score += 1
            if word in [t.lower() for t in m.get("tags", [])]:
                score += 2
            if word in content.lower():
                score += 1

        if score > 0:
            return {**m, "score": score}
        return None

    # AND: 모든 키워드가 포함된 결과만
    results = [r for m in manuals if (r := score_manual(m, required_all=True))]

    # OR 폴백: AND 결과가 없으면 하나라도 매칭되는 결과
    if not results:
        results = [r for m in manuals if (r := score_manual(m, required_all=False))]

    results.sort(key=lambda x: x["score"], reverse=True)
    return {"count": len(results), "manuals": results}


class QARequest(BaseModel):
    question: str
    manual_ids: list[str] = []
    api_key: str = ""


@app.post("/api/qa")
async def qa(req: QARequest):
    """메뉴얼 기반 AI Q&A (2단계: GPT 문서 선택 → 답변 생성)"""
    api_key = req.api_key or os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="OpenAI API 키가 필요합니다.",
        )

    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    manuals = load_index()
    manuals_by_id = {m["id"]: m for m in manuals}

    try:
        # --- Stage 1: GPT가 질문과 관련된 메뉴얼 선택 ---
        if req.manual_ids:
            selected_ids = req.manual_ids
        else:
            catalog = "\n".join(
                f"- id: {m['id']} | 제목: {m['title']} | "
                f"분야: {m['A분야']['대']}/{m['A분야']['중']} | "
                f"태그: {', '.join(m.get('tags', []))}"
                for m in manuals
            )

            selection_response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "당신은 메뉴얼 검색 시스템입니다. "
                            "사용자의 질문과 관련된 메뉴얼의 id를 선택하세요. "
                            "관련된 메뉴얼의 id만 쉼표로 구분하여 반환하세요. "
                            "관련 메뉴얼이 없으면 '없음'이라고 답하세요. "
                            "id 외에 다른 텍스트는 포함하지 마세요."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"## 전체 메뉴얼 목록\n{catalog}\n\n## 질문\n{req.question}",
                    },
                ],
                temperature=0,
                max_tokens=200,
            )

            raw_ids = selection_response.choices[0].message.content.strip()
            if raw_ids == "없음":
                return {
                    "answer": "관련 메뉴얼을 찾지 못했습니다. 다른 질문을 시도해주세요.",
                    "sources": [],
                    "selected_manuals": [],
                }
            selected_ids = [
                id.strip() for id in raw_ids.split(",") if id.strip() in manuals_by_id
            ]

        if not selected_ids:
            return {
                "answer": "관련 메뉴얼을 찾지 못했습니다. 다른 질문을 시도해주세요.",
                "sources": [],
                "selected_manuals": [],
            }

        # --- Stage 2: 선택된 메뉴얼 본문으로 답변 생성 ---
        selected_manuals = []
        context_parts = []
        for mid in selected_ids:
            if mid in manuals_by_id:
                m = manuals_by_id[mid]
                content = load_manual_content(m["file"])
                context_parts.append(f"## {m['title']}\n{content}")
                selected_manuals.append({
                    "id": m["id"],
                    "title": m["title"],
                    "tags": m.get("tags", []),
                })

        context = "\n\n---\n\n".join(context_parts)

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
            "sources": [m["title"] for m in selected_manuals],
            "selected_manuals": selected_manuals,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 응답 생성 실패: {str(e)}")


# --- Auto-categorization ---


class CategorizeRequest(BaseModel):
    file_path: str  # relative to manuals/


@app.post("/api/categorize")
async def auto_categorize(req: CategorizeRequest):
    """GPT로 메뉴얼 자동 분류 (5축 + 태그)"""
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="OpenAI API 키가 필요합니다.")

    content = load_manual_content(req.file_path)

    from openai import OpenAI

    client = OpenAI(api_key=api_key)

    existing_index = load_index()
    existing_values = {
        "A분야_대": sorted(set(m["A분야"]["대"] for m in existing_index)),
        "A분야_중": sorted(set(m["A분야"]["중"] for m in existing_index if m["A분야"]["중"] != "-")),
        "B급수_대": sorted(set(m["B급수"]["대"] for m in existing_index)),
        "B급수_중": sorted(set(m["B급수"]["중"] for m in existing_index)),
        "C홈페이지_대": sorted(set(m["C홈페이지"]["대"] for m in existing_index if m["C홈페이지"]["대"] != "-")),
        "D부서_대": sorted(set(m["D부서"]["대"] for m in existing_index if m["D부서"]["대"] != "-")),
    }

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": f"""당신은 휴텍씨 메뉴얼 분류 시스템입니다.
메뉴얼 내용을 읽고 아래 5축으로 분류하세요.

기존에 사용 중인 값들:
{json.dumps(existing_values, ensure_ascii=False, indent=2)}

가능하면 기존 값을 재사용하되, 맞지 않으면 새 값을 만들어도 됩니다.
해당 없으면 "-"로 표시하세요.

반드시 아래 JSON 형식으로만 응답하세요:
{{
  "title": "메뉴얼 제목",
  "A분야": {{"대": "", "중": "", "소": ""}},
  "B급수": {{"대": "", "중": "", "소": ""}},
  "C홈페이지": {{"대": "", "세부": ""}},
  "D부서": {{"번호": 0, "대": "", "중": ""}},
  "E등급": "",
  "tags": ["태그1", "태그2", "태그3", "태그4", "태그5"]
}}""",
            },
            {
                "role": "user",
                "content": f"파일경로: {req.file_path}\n\n내용:\n{content}",
            },
        ],
        temperature=0.2,
        max_tokens=500,
    )

    result_text = response.choices[0].message.content
    # Extract JSON from response
    try:
        # Handle markdown code blocks
        if "```" in result_text:
            result_text = result_text.split("```")[1]
            if result_text.startswith("json"):
                result_text = result_text[4:]
        categorization = json.loads(result_text.strip())
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail=f"분류 결과 파싱 실패: {result_text}")

    # Generate ID from file path
    file_id = req.file_path.replace("/", "-").replace(".md", "").lower()
    for char in " _()":
        file_id = file_id.replace(char, "-")

    entry = {
        "id": file_id,
        "file": req.file_path,
        **categorization,
    }

    return entry


@app.post("/api/categorize-and-save")
async def categorize_and_save(req: CategorizeRequest):
    """자동 분류 후 _index.json에 저장"""
    entry = await auto_categorize(req)

    index = load_index()
    # 기존 엔트리 업데이트 또는 추가
    found = False
    for i, m in enumerate(index):
        if m["file"] == req.file_path:
            index[i] = entry
            found = True
            break
    if not found:
        index.append(entry)

    save_index(index)
    return {"status": "saved", "entry": entry}
