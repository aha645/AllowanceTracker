"""budget_app — 콘솔 가계부 애플리케이션.

모듈 구성:
    models.py      데이터 모델(dataclass)과 커스텀 예외
    repository.py  transactions.jsonl / transactions.idx / transactions.date.idx 저장 엔진
    stores.py      categories / budgets / recurring JSONL 저장소
    validators.py  입력 검증/정규화 함수
    services.py    거래·예산·반복 규칙 비즈니스 로직
    file_services.py  CSV 가져오기/내보내기, 백업
    formatter.py   외부 라이브러리 없는 표 정렬 출력
    decorators.py  공통 관심사(예외 처리/로그/시간 측정) 데코레이터
    context.py     저장소/서비스 조립(AppContext)
    console.py     커맨드 공용 입출력 보조
    commands/      커맨드 핸들러(거래/집계/카테고리/데이터/반복)
    cli.py         argparse 파서 정의와 main
    __main__.py    `python -m budget_app` 진입점
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
