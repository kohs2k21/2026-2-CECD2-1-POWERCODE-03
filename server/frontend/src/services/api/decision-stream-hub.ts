import { getStoredToken } from '../auth/session';
import { consumeDecisionStream, type InterimDecisionEvent } from './decision-stream.api';
import type { ProcessDecision } from './pipeline.api';

/**
 * 저장 판정 SSE(`GET /api/pipeline/events?include=interims`)를 탭 하나에서 연결 1개로 나눠 쓴다.
 * 알림 배지(모든 화면)와 이상 분석 목록이 각자 연결을 열면 HTTP/1.1 같은 origin 동시 연결 6개 중 2개를 늘 점유한다.
 *
 * - 구독자마다 자기 커서(`after`)를 가진다. 연결은 구독자 커서 중 가장 앞선 위치부터 받고, 각 구독자에게는 자기 커서보다 뒤 이벤트만 준다
 *   (이전에 구독자마다 연결을 따로 열었을 때와 받는 이벤트가 같다). 연결 위치보다 앞선 커서로 새 구독자가 오면 그 커서부터 다시 연결한다.
 * - 끊기면 3초 뒤 연결이 받은 마지막 순번(`Last-Event-ID`)부터 재연결, 로그인 토큰이 바뀌면 재연결하지 않고 멈춘다.
 * - 마지막 구독자가 해제하면 연결을 닫는다(참조 수). 구독 시점 로그인 토큰이 다르면 다른 연결이다.
 * - 다른 탭은 각자 연결을 연다(탭 사이 공유 없음).
 */
export const DECISION_STREAM_RETRY_MS = 3_000;

export type DecisionStreamEvent =
  | { kind: 'decision'; value: ProcessDecision }
  | { kind: 'interim'; value: InterimDecisionEvent };

export type DecisionStreamSubscriber = {
  /** 이 순번 다음 이벤트부터 받는다(`"0"` 이상 정수 문자열). */
  after: string;
  onEvent: (event: DecisionStreamEvent, eventId: string) => void;
  /** 연결이 열렸을 때(이미 열린 연결에 구독하면 바로 한 번). */
  onOpen?: () => void;
  /** 연결이 끊겨 재연결을 기다릴 때. 실패면 오류 문구, 서버가 정상 종료했으면 null. */
  onDisconnect?: (error: string | null) => void;
};

type Subscription = { after: bigint; subscriber: DecisionStreamSubscriber };
type Connection = {
  key: string;
  token: string | null;
  subscriptions: Set<Subscription>;
  /** 다음 연결을 시작할 커서(받은 마지막 순번). */
  position: bigint;
  state: 'connecting' | 'open' | 'retrying' | 'stopped';
  lastError: string | null;
  controller: AbortController | null;
  retry: ReturnType<typeof setTimeout> | undefined;
};

const connections = new Map<string, Connection>();

const cursor = (value: string): bigint => {
  if (!/^(0|[1-9]\d{0,18})$/.test(value)) throw new Error('판정 이벤트 커서 형식이 잘못되었습니다.');
  return BigInt(value);
};

const each = (connection: Connection, call: (subscription: Subscription) => void) => {
  // 콜백 안에서 구독 해제·추가가 일어나도 이번 순회 대상은 바뀌지 않는다.
  for (const subscription of [...connection.subscriptions]) {
    if (connection.subscriptions.has(subscription)) call(subscription);
  }
};

const stop = (connection: Connection) => {
  connection.state = 'stopped';
  connection.controller?.abort();
  connection.controller = null;
  if (connection.retry) clearTimeout(connection.retry);
  connection.retry = undefined;
};

const dispatch = (connection: Connection, controller: AbortController, event: DecisionStreamEvent, eventId: string) => {
  if (controller !== connection.controller) return;
  if (connection.token !== getStoredToken()) {
    stop(connection);
    return;
  }
  const id = BigInt(eventId);
  if (id > connection.position) connection.position = id;
  each(connection, (subscription) => {
    if (id <= subscription.after) return;
    subscription.after = id;
    subscription.subscriber.onEvent(event, eventId);
  });
};

const connect = async (connection: Connection): Promise<void> => {
  if (connection.retry) clearTimeout(connection.retry);
  connection.retry = undefined;
  connection.controller?.abort();
  const controller = new AbortController();
  connection.controller = controller;
  connection.state = connection.state === 'retrying' ? 'retrying' : 'connecting';
  let error: string | null = null;
  try {
    await consumeDecisionStream(
      controller.signal,
      connection.position.toString(),
      (value, id) => dispatch(connection, controller, { kind: 'decision', value }, id),
      () => {
        if (controller !== connection.controller) return;
        connection.state = 'open';
        connection.lastError = null;
        each(connection, (subscription) => subscription.subscriber.onOpen?.());
      },
      (value, id) => dispatch(connection, controller, { kind: 'interim', value }, id),
    );
  } catch (reason) {
    if (!controller.signal.aborted) error = reason instanceof Error ? reason.message : String(reason);
  }
  // 다시 연결(앞선 커서로)·해제·토큰 변경으로 끊은 연결은 여기서 재시도하지 않는다.
  if (controller.signal.aborted || controller !== connection.controller) return;
  connection.controller = null;
  if (connection.token !== getStoredToken()) {
    stop(connection);
    return;
  }
  connection.state = 'retrying';
  if (error !== null) connection.lastError = error;
  each(connection, (subscription) => subscription.subscriber.onDisconnect?.(error));
  connection.retry = setTimeout(() => void connect(connection), DECISION_STREAM_RETRY_MS);
};

/**
 * 판정 이벤트 구독. 같은 로그인 토큰의 구독은 연결 1개를 함께 쓴다. 반환 함수로 해제하고, 마지막 해제 때 연결을 닫는다.
 * 토큰이 없으면 이전 훅과 같이 `consumeDecisionStream`이 로그인 필요 오류를 내고 3초 뒤 다시 시도한다.
 */
export const subscribeDecisionStream = (subscriber: DecisionStreamSubscriber): (() => void) => {
  const token = getStoredToken();
  const key = token ?? '';
  const subscription: Subscription = { after: cursor(subscriber.after), subscriber };
  let connection = connections.get(key);
  if (!connection) {
    connection = { key, token, subscriptions: new Set(), position: subscription.after, state: 'connecting', lastError: null, controller: null, retry: undefined };
    connections.set(key, connection);
    connection.subscriptions.add(subscription);
    void connect(connection);
  } else {
    connection.subscriptions.add(subscription);
    if (connection.state === 'stopped' || subscription.after < connection.position) {
      // 연결이 이미 지나간 이벤트가 필요한 구독자: 그 커서부터 다시 받는다(다른 구독자는 자기 커서로 걸러 중복 없음).
      // 멈춘 연결(토큰이 바뀌었다가 같은 토큰으로 다시 구독)도 여기서 다시 연다.
      if (subscription.after < connection.position) connection.position = subscription.after;
      void connect(connection);
    } else if (connection.state === 'open') {
      subscriber.onOpen?.();
    } else if (connection.state === 'retrying') {
      subscriber.onDisconnect?.(connection.lastError);
    }
  }
  const owner = connection;
  return () => {
    if (!owner.subscriptions.delete(subscription)) return;
    if (owner.subscriptions.size > 0) return;
    stop(owner);
    if (connections.get(owner.key) === owner) connections.delete(owner.key);
  };
};
