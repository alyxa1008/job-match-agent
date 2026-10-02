"""테스트용 가상 데이터. 실제 회사·개인 정보를 쓰지 않는다."""

from agent.schemas import JobPosting


def make_posting(**overrides) -> JobPosting:
    """아무 조건에도 걸리지 않는 가상의 공고. 필요한 필드만 덮어쓴다."""
    base = dict(
        company="(주)에이블랩", title="AI/ML 엔지니어", employment_type="정규직", is_new_grad_only=False,
        min_years=None, max_years=None, location="서울 관악구", job_family="AI/ML 엔지니어",
        required=[], preferred=[], duties=[], flags=[],
    )
    return JobPosting(**{**base, **overrides})
