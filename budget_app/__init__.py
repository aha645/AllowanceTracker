"""budget_app — 콘솔 가계부 애플리케이션.

모듈 구성:
    models.py      데이터 모델(dataclass)과 커스텀 예외
    repository.py  transactions.jsonl / transactions.idx 저장 엔진
    stores.py      categories / budgets / recurring JSONL 저장소
    services.py    검증·검색·요약·예산·CSV 변환 등 비즈니스 로직
    formatter.py   외부 라이브러리 없는 표 정렬 출력
    decorators.py  공통 관심사(예외 처리/로그/시간 측정) 데코레이터
    cli.py         argparse 기반 커맨드 파싱 및 대화형 입력
    __main__.py    `python -m budget_app` 진입점
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
