너는 SQL 개발자(SQLD) 시험 대비 **연습 문제 출제위원**이다. 아래 출제 명세에 맞는 4지선다 문제 1개를 새로 만든다.
기존 기출·문제집·블로그 문제를 옮기거나 바꿔 쓰지 않는다. 표 이름·데이터·상황은 모두 새로 만든다.

[출제 명세]
- 과목: {subject_no}과목 {subject_name}
- 주요항목: {area} / 세부항목: **{item}**
- 문제 형식(kind): **{kind}** — {kind_desc}
- 같은 세부항목의 {variant}번째 문제. 다른 문제와 겹치지 않게 관점을 바꾼다: {angle}

[공통 규칙]
1. 한국어. 시험 문체("다음 중 ~로 옳은 것은?", "~로 가장 적절하지 않은 것은?"). title은 내용 요약 30자 이하(번호·과목명 금지).
2. 정답은 정확히 1개. 나머지 3개는 수험생이 실제로 헷갈리는 오개념에서 나온 그럴듯한 오답.
3. explanation: 정답 근거와 오답 각각이 왜 틀렸는지 4~8문장. 결과 표 값이 필요하면 실제 값을 쓴다.
   concept·sql_pick은 보기 번호 ①~④로 쓴다. **result는 보기 위치를 실행기가 정하므로 번호 대신 [정답]·[오답1]·[오답2]·[오답3]**
   (오답 번호 = distractor_sqls 순서)으로 쓰고, 네 표시를 모두 한 번 이상 쓴다.
3-1. stem에는 **질문 문장만** 쓴다(sql_pick은 요구 결과 설명까지). 표 데이터·SQL·보기는 stem에 넣지 않는다 — 화면이 setup_sql로 만든 표와
   query_sql·보기를 따로 보여 준다.
4. 표·열 이름은 영어 대문자(예: EMP, DEPT_NO), 데이터 값은 짧게. 문장에 "출제 명세", "kind", "변형" 같은 지시어를 쓰지 않는다.
5. 데이터 모델링 개념은 SQLD 표준 용어(엔터티, 속성, 관계, 식별자, 정규형 등)를 쓴다.

[SQL 규칙 — result·sql_pick]
6. 모든 SQL은 **PostgreSQL 16에서 그대로 실행**되어야 한다(실행기가 실제로 돌려 정답을 계산한다). 표준 SQL을 쓴다.
   - 쓰지 말 것: ROWNUM, CONNECT BY, (+) 외부 조인, DECODE, MINUS(→ EXCEPT), DUAL, PIVOT/UNPIVOT, TO_CHAR 형식 차이에 기대는 문제,
     트랜잭션 제어(COMMIT·ROLLBACK·SAVEPOINT), GRANT/REVOKE, 함수·트리거 생성, 시스템 카탈로그.
   - 쓸 수 있음: NVL·NVL2(실행기에 있음), COALESCE, NULLIF, CASE, 윈도우 함수, ROLLUP·CUBE·GROUPING SETS·GROUPING(),
     WITH RECURSIVE(계층 질의), FETCH FIRST n ROWS ONLY(Top N), REGEXP_SUBSTR·REGEXP_COUNT·REGEXP_REPLACE·REGEXP_LIKE(정규 표현식), EXCEPT·INTERSECT·UNION ALL.
7. setup_sql: CREATE TABLE 1~3개 + INSERT. 표 하나에 행 4~8개. NULL·중복 등 문제 핵심을 드러내는 값을 꼭 넣는다. 날짜는 DATE '2026-01-05' 형식.
8. 결과가 여러 행이면 query_sql·기준 SQL에 반드시 ORDER BY(행 순서가 하나로 정해지게). 결과는 5열·8행 이하.
9. 숫자 나눗셈은 정수 나눗셈 차이가 생기지 않게 하거나 ROUND로 고정한다.

[kind별 출력]
- concept: choices_text 4개(보기 문장), answer(1~4). setup_sql·query_sql·options_sql·distractor_sqls는 빈 값.
- result: setup_sql, query_sql(문제에 보여 줄 SQL — 여러 문장이면 ;로 구분, 예: UPDATE …; SELECT …. 마지막은 SELECT),
  distractor_sqls 3개(정답 SQL을 **한 군데만** 흔한 실수로 바꾼 SQL — 예: WHERE 대신 HAVING 위치 착각, OUTER→INNER, NULL 비교, COUNT(*)↔COUNT(열),
  RANK↔DENSE_RANK, UNION↔UNION ALL. 실행 결과가 정답과 달라야 하고 서로도 달라야 한다). 보기는 실행기가 결과 표로 만든다 → choices_text는 빈 목록, answer는 0.
- sql_pick: setup_sql, requirement(원하는 결과를 말로 설명, stem에도 포함), reference_sql(요구를 정확히 만족하는 기준 SQL, 보이지 않음),
  options_sql 4개, mode="match"(요구를 만족하는 SQL은 정확히 1개) 또는 "mismatch"(만족하지 않는 SQL이 정확히 1개), answer(1~4).
  실행기가 각 보기를 실행해 기준 SQL 결과와 비교해 answer를 확인한다. 오답 보기는 문법 오류보다 **결과가 달라지는 실수**로.
{feedback}
