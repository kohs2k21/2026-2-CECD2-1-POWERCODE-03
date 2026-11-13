// 시험용 가짜 LLM /health(llm-recommendation-job.md '생성 서비스 가용성' 절). 실제 llm 컨테이너·GPU를 부르지 않는다.
// mode: ready(200 {"status":"ready"}) / down(503) / loading(200 {"status":"loading"}) / garbage(200 JSON 아님) / hang(응답 없음)
import http from 'node:http';

export async function startFakeLlmHealth(mode = 'ready', { delayMs = 0 } = {}) {
  const state = { mode, hits: 0, delayMs };
  const server = http.createServer((request, response) => {
    state.hits += 1;
    const reply = () => {
      if (request.url !== '/health') { response.writeHead(404).end(); return; }
      if (state.mode === 'hang') return;
      const json = (status, body) => response.writeHead(status, { 'Content-Type': 'application/json' }).end(body);
      if (state.mode === 'ready') json(200, '{"status":"ready"}');
      else if (state.mode === 'loading') json(200, '{"status":"loading"}');
      else if (state.mode === 'garbage') json(200, 'not json');
      else json(503, '{"code":"unavailable"}');
    };
    if (state.delayMs) setTimeout(reply, state.delayMs); else reply();
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  return {
    state,
    url: `http://127.0.0.1:${server.address().port}/health`,
    close: () => new Promise((resolve) => { server.closeAllConnections(); server.close(resolve); }),
  };
}
