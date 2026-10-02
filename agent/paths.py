"""프로젝트 안의 파일 위치. 경로는 여기서만 정한다."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = ROOT / "config" / "profile.yaml"
RESUME_PATH = ROOT / "data" / "resume.md"
LLM_CACHE_DIR = ROOT / "data" / "llm_cache"
