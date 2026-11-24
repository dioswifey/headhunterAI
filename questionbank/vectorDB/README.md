# vectorDB — 런타임 생성 디렉토리

Chroma 벡터 저장소가 여기에 생성된다. `.gitignore`로 제외되며,
`internal_docs_agent ingest`를 실행하면 `internal_documents/`의 원본에서 다시 만들어진다.
`docker-compose.yml`이 이 경로를 볼륨으로 마운트하므로 디렉토리만 남겨 둔다.
