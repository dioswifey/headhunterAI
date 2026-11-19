너는 IT 기업 코딩테스트 출제위원이다. 아래 출제 명세 하나에 맞는 **새로운** Python 코딩 문제 하나를 처음부터 설계한다.

[출제 명세]
- 주제(topic): {topic}
- 난이도: {difficulty} ({level_desc})
- 학습 목표: {learning_objective}
- 목표 복잡도: {target_complexity}
- 입출력 구조(필수): {structure}  — 화살표 왼쪽 = 매개변수 타입 묶음, 오른쪽 = 반환 타입. 숫자 = int/float, 1차원 배열 = list[int], 2차원 배열 = list[list[...]], 문자열 = str, 문자열 배열 = list[str], 참/거짓 = bool. **매개변수와 반환 타입을 정확히 이 구조로 만든다**
{feedback}
[독창성 규칙]
- 특정 사이트(프로그래머스·LeetCode·백준 등)의 문제나 널리 알려진 대표 문제를 기억해서 재현하지 않는다. 이름·숫자만 바꾼 변형도 안 된다.
- 상황(이야기)·입력 형태·조건·반환 규칙을 이 명세에 맞춰 새로 설계한다.
- 이 지시문의 문장('작성해라', 'JSON', '출제 명세' 등)을 문제 설명에 옮겨 쓰지 않는다.

[간결성 규칙] — 실제 지원자가 짧은 시간에 읽고 풀어야 한다. 짧고 담백하게 쓴다.
- 상황 설정(스토리)은 **1문장 이하**로 끝낸다. 장식적인 배경(물류센터·전시장·포장대 같은 가상의 업무 무대)을 꾸미지 않는다 — "배열/문자열이 주어질 때 무엇을 구하라"로 바로 들어가도 된다.
- 규칙(변환 규칙·조건)은 한 번만, 명확한 문장으로 설명한다. 같은 규칙을 다른 말로 바꿔 다시 설명하거나 강조를 위해 반복하지 않는다(나쁜 예: "A면 B를 한다"라고 쓴 뒤 "비교할 때는 A가 아니라 B를 기준으로 한다"처럼 같은 내용을 또 설명).
- **난이도가 basic이면 예외·분기 규칙은 최대 1개만 둔다.** "A이면 B, 그런데 C인 경우는 D, 그마저 E면 F" 식으로 규칙을 2개 이상 쌓지 않는다 — 배열·문자열을 한 번 훑으면(단일 순회, 중첩 조건 분기 없이) 바로 답이 나오는 문제여야 한다. applied는 규칙 2개까지 허용하되 그 이상은 쌓지 않는다.
- statement 안에 구체적인 숫자로 풀어보는 예시(walkthrough)를 넣지 않는다 — 예시 설명은 별도 섹션으로 자동 추가되므로, statement는 규칙 자체만 설명한다.
- **난이도가 basic이면 statement 전체 분량은 100~400자를 목표로 한다**(참고 기준: "문자열 text를 공백으로 나눈 단어 중 가장 많이 나온 k개를 반환하세요" 수준의 짧은 문제가 적정선이다). applied는 400~600자. 꼭 필요한 정보(상황·규칙·반환 대상)만 남기고 나머지는 뺀다.

[작성 규칙]
1. title: 한국어 제목, 40자 이하.
2. statement: 한국어. 상황 설명과 함수가 할 일. 답이 여러 개일 수 있으면 어느 것을 반환하는지 정한다. 모호한 표현("적당히", "충분히 큰") 금지. 위 [간결성 규칙]을 반드시 따른다.
3. io_spec: 한국어. 매개변수마다 이름·타입·의미, 반환 값의 타입·의미.
4. constraints: 제약 조건 목록(각 항목 한 줄, 숫자로). 길이·값 범위, **빈 입력 허용 여부**, **중복 허용 여부**를 반드시 명시한다.
5. func_name: 영어 snake_case. params: 매개변수 이름 목록(영어, 1~3개).
6. solution: 목표 복잡도를 만족하는 정답 코드. `def <func_name>(...)` (도우미 함수는 그 안이나 위에). 표준 라이브러리만. input()·print()·open()·os·sys·subprocess·socket 금지. 반환 값은 int·float·str·bool·list만(tuple·set·dict 금지).
7. solution_explanation: 한국어로 풀이 핵심 3~5문장. time_complexity, space_complexity: 예 "O(n)".
8. brute: 같은 함수 이름의 **느리지만 확실한** 단순 풀이(완전탐색 등). 작은 입력에서 solution과 결과가 같아야 한다.
9. validator: `def validate(<params와 같은 순서>):` 입력이 constraints를 모두 지키면 True, 아니면 False.
10. gen_examples: `def gen_examples():` 설명용 예시 입력 **정확히 3개**의 목록을 반환. 각 원소 = 매개변수 순서대로 담은 list. 작고 이해하기 쉬운 값.
11. gen_edge: `def gen_edge():` 경계값 입력 4~8개의 목록을 반환(최소 크기, 빈 입력이 허용되면 빈 입력, 같은 값 반복, 값의 최솟값·최댓값, 답이 없거나 특수한 경우). 각 원소는 짧게(직렬화했을 때 200자 이하).
12. gen_random: `def gen_random(seed):` 작은 무작위 입력 하나(매개변수 list)를 반환. 첫 줄 `rng = random.Random(seed)`, 이후 `rng`만 사용. 크기는 단순 풀이가 빨리 끝나도록 작게(배열 길이 8 이하, 격자 5×5 이하 등). 반드시 constraints를 지킨다.
13. gen_large: `def gen_large(seed):` constraints의 최대 크기 입력 하나(매개변수 list). `rng = random.Random(seed)`.
14. wrong_solutions: **틀린** 풀이 2개(문자열 목록). 만드는 방법: **solution을 그대로 복사한 뒤 버그 1개만 일부러 넣는다**(예: `>`를 `>=`로, 초기값 0을 1로, 빈 입력·단일 원소 처리 누락, 동점 처리 반대로). 다른 방식의 올바른 구현을 내면 안 된다. 각 코드 첫 줄에 `# 버그: <무엇을 틀리게 했는지>` 주석. 두 버그는 서로 달라야 하고, gen_edge·gen_random의 입력 중 적어도 하나에서 정답과 다른 값을 내야 한다.

gen_* 함수와 validator 안에서는 `import random`(gen_random·gen_large)만 쓸 수 있다. 다른 입출력·네트워크 금지.

[출력] JSON: title, statement, io_spec, constraints, func_name, params, solution, solution_explanation, time_complexity, space_complexity, brute, validator, gen_examples, gen_edge, gen_random, gen_large, wrong_solutions
