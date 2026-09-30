import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import jwt from "jsonwebtoken";

const testsDirectory = path.dirname(fileURLToPath(import.meta.url));
const backendDirectory = path.resolve(testsDirectory, "..");
const entrypoint = path.join(backendDirectory, "dist", "index.js");

const ADMIN = {
  email: "qa-admin@example.test",
  password: "QA-admin-local-7b2d!",
};
const USER = {
  email: "qa-user@example.test",
  password: "QA-user-local-7b2d!",
};
const JWT_SECRET = "qa-only-local-jwt-secret";

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function getFreePort() {
  const probe = net.createServer();
  await new Promise((resolve, reject) => {
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", resolve);
  });
  const address = probe.address();
  const port = typeof address === "object" && address ? address.port : null;
  await new Promise((resolve, reject) => {
    probe.close((error) => (error ? reject(error) : resolve()));
  });
  assert.equal(typeof port, "number", "the OS must provide an ephemeral port");
  return port;
}

function cleanEnvironment() {
  const environment = { ...process.env };

  // Do not pass credentials from the host into this local-only test process.
  for (const key of Object.keys(environment)) {
    if (
      /(SECRET|PASSWORD|TOKEN|API[_-]?KEY|PRIVATE[_-]?KEY|SMTP|OPENAI|DATABASE_URL|PEM|RAWDATA|BODY)/i.test(
        key,
      )
    ) {
      delete environment[key];
    }
  }

  delete environment.JWT_SECRET;
  delete environment.JWT_EXPIRES_IN;
  delete environment.USER_DATA_PATH;
  delete environment.SEED_ADMIN_EMAIL;
  delete environment.SEED_ADMIN_PASSWORD;
  delete environment.SEED_USER_EMAIL;
  delete environment.SEED_USER_PASSWORD;
  delete environment.PORT;
  return environment;
}

function makeEnvironment({ runtimeDirectory, dataPath, port, secret, seed = true, partialSeed = false }) {
  const environment = cleanEnvironment();
  environment.NODE_ENV = "test";
  // dotenv only sees this nonexistent path because the child cwd is an empty temp directory.
  environment.DOTENV_CONFIG_PATH = path.join(runtimeDirectory, "no-local-env-file");
  environment.PORT = String(port);
  environment.USER_DATA_PATH = dataPath;

  if (secret !== undefined) {
    environment.JWT_SECRET = secret;
  }

  if (seed) {
    environment.SEED_ADMIN_EMAIL = ADMIN.email;
    environment.SEED_ADMIN_PASSWORD = ADMIN.password;
    environment.SEED_USER_EMAIL = USER.email;
    if (!partialSeed) {
      environment.SEED_USER_PASSWORD = USER.password;
    }
  }

  return environment;
}

function spawnBackend({ runtimeDirectory, environment }) {
  const child = spawn(process.execPath, [entrypoint], {
    cwd: runtimeDirectory,
    env: environment,
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  let output = "";
  const collect = (chunk) => {
    // Keep enough startup diagnostics for assertions without ever printing them.
    if (output.length < 16_000) {
      output += chunk.toString();
    }
  };
  child.stdout.on("data", collect);
  child.stderr.on("data", collect);
  return {
    child,
    getOutput: () => output,
  };
}

async function waitForExit(processHandle, timeoutMilliseconds = 5_000) {
  const { child, getOutput } = processHandle;
  if (child.exitCode !== null) {
    return { code: child.exitCode, signal: child.signalCode, output: getOutput() };
  }

  return new Promise((resolve) => {
    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ ...result, output: getOutput() });
    };
    const timer = setTimeout(() => {
      child.kill();
      finish({ code: null, signal: "timeout" });
    }, timeoutMilliseconds);
    child.once("exit", (code, signal) => finish({ code, signal }));
  });
}

async function stopBackend(processHandle) {
  if (!processHandle || processHandle.child.exitCode !== null) return;
  processHandle.child.kill();
  await waitForExit(processHandle, 5_000);
}

async function fetchWithTimeout(url, options = {}, timeoutMilliseconds = 5_000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMilliseconds);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function requestJson(baseUrl, route, options = {}) {
  const response = await fetchWithTimeout(`${baseUrl}${route}`, options);
  const text = await response.text();
  let body = null;
  try {
    body = JSON.parse(text);
  } catch {
    // The status and headers still provide the contract assertion for non-JSON errors.
  }
  return { status: response.status, headers: response.headers, body };
}

async function requestJsonMethod(baseUrl, route, { method, token, body }) {
  const headers = { "content-type": "application/json" };
  if (token) headers.authorization = `Bearer ${token}`;
  const result = await requestJson(baseUrl, route, {
    method,
    headers,
    body: JSON.stringify(body ?? {}),
  });
  return result;
}

async function readNextSseEvent(response, timeoutMilliseconds = 1_000) {
  assert.ok(response.body, "an authenticated SSE response must have a body");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const deadline = Date.now() + timeoutMilliseconds;

  try {
    while (Date.now() < deadline) {
      const remaining = Math.max(1, deadline - Date.now());
      let timer;
      const result = await Promise.race([
        reader.read(),
        new Promise((resolve) => {
          timer = setTimeout(() => resolve({ timeout: true }), remaining);
        }),
      ]);
      clearTimeout(timer);

      if (result?.timeout || result?.done) return null;
      buffer += decoder.decode(result.value, { stream: true }).replace(/\r\n/g, "\n");

      let delimiterIndex = buffer.indexOf("\n\n");
      while (delimiterIndex !== -1) {
        const frame = buffer.slice(0, delimiterIndex);
        buffer = buffer.slice(delimiterIndex + 2);
        const dataLines = frame
          .split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).replace(/^ /, ""));
        if (dataLines.length > 0) {
          try {
            return JSON.parse(dataLines.join("\n"));
          } catch {
            // Ignore non-JSON control frames and keep reading for an anomaly.
          }
        }
        delimiterIndex = buffer.indexOf("\n\n");
      }
    }

    return null;
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

function jsonOptions({ token, body } = {}) {
  const headers = { "content-type": "application/json" };
  if (token) headers.authorization = `Bearer ${token}`;
  return {
    method: "POST",
    headers,
    body: JSON.stringify(body ?? {}),
  };
}

function expectStatus(result, expected, label) {
  assert.equal(result.status, expected, `${label} returned HTTP ${result.status}`);
}

function expectCode(result, expected, label) {
  assert.equal(result.body?.code, expected, `${label} returned code ${result.body?.code}`);
}

async function waitForHealth(baseUrl, processHandle) {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    if (processHandle.child.exitCode !== null) {
      throw new Error(`backend exited before health check (code ${processHandle.child.exitCode})`);
    }
    try {
      const result = await requestJson(baseUrl, "/health");
      if (result.status === 200) return;
    } catch {
      // The listener may still be starting.
    }
    await sleep(100);
  }
  throw new Error("backend did not become healthy within 8 seconds");
}

async function login(baseUrl, credentials) {
  const result = await requestJson(baseUrl, "/api/auth/login", jsonOptions({ body: credentials }));
  expectStatus(result, 200, `${credentials.email} login`);
  assert.equal(typeof result.body?.token, "string", "login must return a token");
  assert.equal(result.body?.user?.email, credentials.email, "login must return the account");
  return result.body.token;
}

async function readUsers(dataPath) {
  const parsed = JSON.parse(await readFile(dataPath, "utf8"));
  assert.ok(Array.isArray(parsed), "the user store must be a JSON array");
  return parsed;
}

async function main() {
  assert.ok(existsSync(entrypoint), "run npm run build before the backend integration harness");

  const sandbox = await mkdtemp(path.join(os.tmpdir(), "esb-backend-qa-"));
  const runtimeDirectory = path.join(sandbox, "runtime");
  await mkdir(runtimeDirectory, { recursive: true });
  const dataPath = path.join(sandbox, "users.json");
  let serverProcess = null;

  try {
    // A missing secret must stop startup instead of activating a fallback secret.
    const missingSecretPort = await getFreePort();
    const missingSecret = spawnBackend({
      runtimeDirectory,
      environment: makeEnvironment({
        runtimeDirectory,
        dataPath: path.join(sandbox, "missing-secret.json"),
        port: missingSecretPort,
        secret: undefined,
        seed: false,
      }),
    });
    const missingSecretExit = await waitForExit(missingSecret);
    assert.notEqual(missingSecretExit.code, 0, "startup without JWT_SECRET must fail");
    assert.match(missingSecretExit.output, /JWT_SECRET is required/);

    // A partial seeded-account pair is also a startup configuration error.
    const partialSeedPort = await getFreePort();
    const partialSeed = spawnBackend({
      runtimeDirectory,
      environment: makeEnvironment({
        runtimeDirectory,
        dataPath: path.join(sandbox, "partial-seed.json"),
        port: partialSeedPort,
        secret: JWT_SECRET,
        partialSeed: true,
      }),
    });
    const partialSeedExit = await waitForExit(partialSeed);
    assert.notEqual(partialSeedExit.code, 0, "partial seeded-account configuration must fail");
    assert.match(partialSeedExit.output, /must be provided together/);

    const port = await getFreePort();
    const baseUrl = `http://127.0.0.1:${port}`;
    serverProcess = spawnBackend({
      runtimeDirectory,
      environment: makeEnvironment({ runtimeDirectory, dataPath, port, secret: JWT_SECRET }),
    });
    await waitForHealth(baseUrl, serverProcess);

    const firstUsers = await readUsers(dataPath);
    assert.equal(firstUsers.length, 2, "the first boot must create exactly the configured seed accounts");
    assert.deepEqual(
      firstUsers.map(({ id, email, userType }) => ({ id, email, userType })),
      [
        { id: "1", email: ADMIN.email, userType: "admin" },
        { id: "2", email: USER.email, userType: "user" },
      ],
      "seed account IDs, emails, and roles must be deterministic",
    );
    for (const account of firstUsers) {
      assert.equal(typeof account.password, "string", "seed passwords must be persisted as hashes");
      assert.notEqual(account.password, ADMIN.password, "the JSON store must not contain plaintext passwords");
      assert.notEqual(account.password, USER.password, "the JSON store must not contain plaintext passwords");
    }

    const missingAuth = await requestJson(baseUrl, "/api/auth/me");
    expectStatus(missingAuth, 401, "profile without authentication");
    const basicAuth = await requestJson(baseUrl, "/api/auth/me", {
      headers: { authorization: "Basic qa-token" },
    });
    expectStatus(basicAuth, 401, "profile with a non-Bearer authorization scheme");
    const invalidAuth = await requestJson(baseUrl, "/api/auth/me", {
      headers: { authorization: "Bearer invalid-token" },
    });
    expectStatus(invalidAuth, 403, "profile with an invalid Bearer token");
    const expiredToken = jwt.sign(
      { id: "1", email: ADMIN.email, userType: "admin" },
      JWT_SECRET,
      { expiresIn: -1 },
    );
    const expiredAuth = await requestJson(baseUrl, "/api/auth/me", {
      headers: { authorization: `Bearer ${expiredToken}` },
    });
    expectStatus(expiredAuth, 403, "profile with an expired Bearer token");

    const incompleteLogin = await requestJson(
      baseUrl,
      "/api/auth/login",
      jsonOptions({ body: { email: ADMIN.email } }),
    );
    expectStatus(incompleteLogin, 400, "login with a missing password");
    const wrongPassword = await requestJson(
      baseUrl,
      "/api/auth/login",
      jsonOptions({ body: { email: ADMIN.email, password: "wrong-local-password" } }),
    );
    expectStatus(wrongPassword, 401, "login with a wrong password");

    const adminToken = await login(baseUrl, ADMIN);
    const userToken = await login(baseUrl, USER);
    const profile = await requestJson(baseUrl, "/api/auth/me", {
      headers: { authorization: `Bearer ${userToken}` },
    });
    expectStatus(profile, 200, "authenticated profile");
    assert.equal(profile.body?.user?.userType, "user");

    const usersWithoutAuth = await requestJson(baseUrl, "/api/users");
    expectStatus(usersWithoutAuth, 401, "user list without authentication");
    const usersWithUserRole = await requestJson(baseUrl, "/api/users", {
      headers: { authorization: `Bearer ${userToken}` },
    });
    expectStatus(usersWithUserRole, 403, "user list with a non-admin account");
    const usersWithAdminRole = await requestJson(baseUrl, "/api/users", {
      headers: { authorization: `Bearer ${adminToken}` },
    });
    expectStatus(usersWithAdminRole, 200, "user list with an admin account");
    assert.ok(Array.isArray(usersWithAdminRole.body));
    assert.ok(usersWithAdminRole.body.every((user) => !Object.hasOwn(user, "password")));

    const validLog = {
      logId: "qa-valid-ingest-1",
      detectedAt: "2026-01-01T12:00:00.000Z",
      processName: "QA Process",
      channelName: "qa-channel",
      transactionId: "qa-transaction-1",
      status: "error",
      anomalyScore: 0.5,
      processTimeMs: 1_000,
      responseCode: "4001",
    };
    const evaluatedRisk = await requestJson(
      baseUrl,
      "/api/anomaly/evaluate-risk",
      jsonOptions({ token: userToken, body: { logs: [validLog] } }),
    );
    expectStatus(evaluatedRisk, 200, "valid risk evaluation");
    assert.deepEqual(evaluatedRisk.body?.results, [
      { logId: validLog.logId, riskScore: 55, riskLevel: 2, severity: "Warning" },
    ]);

    for (const endpoint of ["/send-code", "/verify-code", "/register"]) {
      const registration = await requestJson(
        baseUrl,
        `/api/auth${endpoint}`,
        jsonOptions({
          body: {
            email: "disabled-registration@example.test",
            password: "disabled-local-password",
            code: "000000",
          },
        }),
      );
      expectStatus(registration, 410, `${endpoint} registration flow`);
    }

    const anomalyWithoutAuth = await requestJson(
      baseUrl,
      "/api/anomaly/evaluate-risk",
      jsonOptions({ body: {} }),
    );
    expectStatus(anomalyWithoutAuth, 401, "anomaly evaluation without authentication");
    const malformedEvaluation = await requestJson(
      baseUrl,
      "/api/anomaly/evaluate-risk",
      jsonOptions({ token: adminToken, body: {} }),
    );
    expectStatus(malformedEvaluation, 400, "anomaly evaluation with an invalid payload");
    const anomalyLogWithoutAuth = await requestJson(
      baseUrl,
      "/api/anomaly/logs",
      jsonOptions({ body: {} }),
    );
    expectStatus(anomalyLogWithoutAuth, 401, "anomaly ingestion without authentication");
    const malformedAnomalyLog = await requestJson(
      baseUrl,
      "/api/anomaly/logs",
      jsonOptions({ token: adminToken, body: {} }),
    );
    expectStatus(malformedAnomalyLog, 400, "anomaly ingestion with an invalid payload");

    const sseWithoutAuth = await fetchWithTimeout(`${baseUrl}/api/anomaly/realtime-stream`);
    expectStatus({ status: sseWithoutAuth.status }, 401, "SSE without authentication");
    await sseWithoutAuth.text();
    const sseWithInvalidAuth = await fetchWithTimeout(`${baseUrl}/api/anomaly/realtime-stream`, {
      headers: { authorization: "Bearer invalid-token" },
    });
    expectStatus({ status: sseWithInvalidAuth.status }, 403, "SSE with an invalid token");
    await sseWithInvalidAuth.text();

    const sseResponse = await fetchWithTimeout(`${baseUrl}/api/anomaly/realtime-stream`, {
      headers: { authorization: `Bearer ${adminToken}` },
    });
    expectStatus({ status: sseResponse.status }, 200, "authenticated SSE");
    assert.match(
      sseResponse.headers.get("content-type") || "",
      /text\/event-stream/i,
      "authenticated SSE must use the event-stream content type",
    );
    const userIngest = await requestJson(
      baseUrl,
      "/api/anomaly/logs",
      jsonOptions({ token: userToken, body: validLog }),
    );
    expectStatus(userIngest, 403, "real-time ingest with a non-admin account");

    const validIngest = await requestJson(
      baseUrl,
      "/api/anomaly/logs",
      jsonOptions({ token: adminToken, body: validLog }),
    );
    expectStatus(validIngest, 201, "valid admin real-time ingest");
    const expectedEvent = {
      schemaVersion: 1,
      eventId: validLog.logId,
      provenance: "backend-ingest",
      detectedAt: validLog.detectedAt,
      processName: validLog.processName,
      channelName: validLog.channelName,
      transactionId: validLog.transactionId,
      status: validLog.status,
      responseCode: validLog.responseCode,
      anomalyScore: validLog.anomalyScore,
      processTimeMs: validLog.processTimeMs,
      riskScore: 55,
      riskLevel: 2,
      severity: "Warning",
    };
    assert.deepEqual(validIngest.body?.log, expectedEvent, "ingest must return the normalized event contract");
    const deliveredEvent = await readNextSseEvent(sseResponse);
    assert.deepEqual(deliveredEvent, expectedEvent, "admin ingest must reach a live SSE subscriber");

    for (const [label, body] of [
      ["malformed email", { email: "not-an-email" }],
      ["empty password", { password: "" }],
      ["malformed role", { userType: "superuser" }],
      ["non-string email", { email: 42 }],
      ["non-string password", { password: 42 }],
    ]) {
      const invalidUpdate = await requestJsonMethod(baseUrl, "/api/users/2", {
        method: "PUT",
        token: adminToken,
        body,
      });
      expectStatus(invalidUpdate, 400, `${label} user update`);
      expectCode(invalidUpdate, "invalid_user_update", `${label} user update`);
    }

    const promoteUser = await requestJsonMethod(baseUrl, "/api/users/2", {
      method: "PUT",
      token: adminToken,
      body: { userType: "admin" },
    });
    expectStatus(promoteUser, 200, "promoting a user account");
    const staleUserRole = await requestJson(baseUrl, "/api/users", {
      headers: { authorization: `Bearer ${userToken}` },
    });
    expectStatus(staleUserRole, 401, "previous user token after promotion");
    expectCode(staleUserRole, "token_revoked", "previous user token after promotion");

    const promotedToken = await login(baseUrl, USER);
    const promotedUsers = await requestJson(baseUrl, "/api/users", {
      headers: { authorization: `Bearer ${promotedToken}` },
    });
    expectStatus(promotedUsers, 200, "new admin token after promotion");

    const promotedStream = await fetchWithTimeout(`${baseUrl}/api/anomaly/realtime-stream`, {
      headers: { authorization: `Bearer ${promotedToken}` },
    });
    expectStatus({ status: promotedStream.status }, 200, "SSE for a promoted account");
    const demoteUser = await requestJsonMethod(baseUrl, "/api/users/2", {
      method: "PUT",
      token: adminToken,
      body: { userType: "user" },
    });
    expectStatus(demoteUser, 200, "demoting a user account");
    const staleAdminRole = await requestJson(baseUrl, "/api/users", {
      headers: { authorization: `Bearer ${promotedToken}` },
    });
    expectStatus(staleAdminRole, 401, "previous admin token after demotion");
    expectCode(staleAdminRole, "token_revoked", "previous admin token after demotion");
    const demotedStreamIngest = await requestJson(
      baseUrl,
      "/api/anomaly/logs",
      jsonOptions({
        token: adminToken,
        body: { ...validLog, logId: "qa-demoted-stream-1" },
      }),
    );
    expectStatus(demotedStreamIngest, 201, "ingest after subscriber demotion");
    assert.equal(
      await readNextSseEvent(promotedStream, 500),
      null,
      "a demoted subscriber must not receive events from its already-open stream",
    );

    const demotedToken = await login(baseUrl, USER);
    const demotedUsers = await requestJson(baseUrl, "/api/users", {
      headers: { authorization: `Bearer ${demotedToken}` },
    });
    expectStatus(demotedUsers, 403, "fresh user token after demotion");

    const changedEmail = "qa-renamed-user@example.test";
    const renameUser = await requestJsonMethod(baseUrl, "/api/users/2", {
      method: "PUT",
      token: adminToken,
      body: { email: changedEmail },
    });
    expectStatus(renameUser, 200, "changing an account email");
    const staleEmailToken = await requestJson(baseUrl, "/api/auth/me", {
      headers: { authorization: `Bearer ${demotedToken}` },
    });
    expectStatus(staleEmailToken, 401, "token after account email change");
    expectCode(staleEmailToken, "token_revoked", "token after account email change");
    const restoreEmail = await requestJsonMethod(baseUrl, "/api/users/2", {
      method: "PUT",
      token: adminToken,
      body: { email: USER.email },
    });
    expectStatus(restoreEmail, 200, "restoring the test account email");

    const expiryToken = jwt.sign(
      { id: "2", email: USER.email, userType: "user" },
      JWT_SECRET,
      { expiresIn: "3s" },
    );
    const expiringStream = await fetchWithTimeout(`${baseUrl}/api/anomaly/realtime-stream`, {
      headers: { authorization: `Bearer ${expiryToken}` },
    });
    expectStatus({ status: expiringStream.status }, 200, "SSE connection before token expiry");
    await sleep(3_200);
    const expiredStreamIngest = await requestJson(
      baseUrl,
      "/api/anomaly/logs",
      jsonOptions({
        token: adminToken,
        body: { ...validLog, logId: "qa-expired-stream-1" },
      }),
    );
    expectStatus(expiredStreamIngest, 201, "ingest after subscriber token expiry");
    assert.equal(
      await readNextSseEvent(expiringStream, 500),
      null,
      "an expired subscriber must not receive events from its already-open stream",
    );

    const raceAccounts = [
      {
        id: "3",
        email: "qa-race-a@example.test",
        password: "not-used-in-auth-tests",
        userType: "user",
        createdAt: "2026-01-01T00:00:00.000Z",
      },
      {
        id: "4",
        email: "qa-race-b@example.test",
        password: "not-used-in-auth-tests",
        userType: "user",
        createdAt: "2026-01-01T00:00:00.000Z",
      },
    ];
    await writeFile(
      dataPath,
      JSON.stringify([...await readUsers(dataPath), ...raceAccounts], null, 2),
      "utf8",
    );
    const raceStoreText = await readFile(dataPath, "utf8");
    const raceUsers = await readUsers(dataPath);

    await stopBackend(serverProcess);
    serverProcess = null;

    const restartPort = await getFreePort();
    const restartUrl = `http://127.0.0.1:${restartPort}`;
    serverProcess = spawnBackend({
      runtimeDirectory,
      environment: makeEnvironment({
        runtimeDirectory,
        dataPath,
        port: restartPort,
        secret: JWT_SECRET,
      }),
    });
    await waitForHealth(restartUrl, serverProcess);
    const restartAdminToken = await login(restartUrl, ADMIN);
    const secondStoreText = await readFile(dataPath, "utf8");
    assert.equal(
      secondStoreText,
      raceStoreText,
      "restarting must preserve local account changes without duplicate seeds",
    );
    assert.deepEqual(await readUsers(dataPath), raceUsers);

    const duplicateEmail = "qa-parallel-duplicate@example.test";
    const concurrentUpdates = await Promise.all([
      requestJsonMethod(restartUrl, "/api/users/3", {
        method: "PUT",
        token: restartAdminToken,
        body: { email: duplicateEmail },
      }),
      requestJsonMethod(restartUrl, "/api/users/4", {
        method: "PUT",
        token: restartAdminToken,
        body: { email: duplicateEmail },
      }),
    ]);
    assert.deepEqual(
      concurrentUpdates.map((result) => result.status).sort(),
      [200, 400],
      "parallel updates must permit exactly one account to claim a duplicate email",
    );
    const conflict = concurrentUpdates.find((result) => result.status === 400);
    expectCode(conflict, "email_already_in_use", "parallel duplicate email update");
    const postRaceUsers = await readUsers(dataPath);
    assert.equal(
      postRaceUsers.filter((account) => account.email === duplicateEmail).length,
      1,
      "the JSON store must contain only one account with the winning email",
    );

    const finalStoreText = await readFile(dataPath, "utf8");
    await stopBackend(serverProcess);
    serverProcess = null;
    const finalPort = await getFreePort();
    const finalUrl = `http://127.0.0.1:${finalPort}`;
    serverProcess = spawnBackend({
      runtimeDirectory,
      environment: makeEnvironment({
        runtimeDirectory,
        dataPath,
        port: finalPort,
        secret: JWT_SECRET,
      }),
    });
    await waitForHealth(finalUrl, serverProcess);
    await login(finalUrl, ADMIN);
    assert.equal(
      await readFile(dataPath, "utf8"),
      finalStoreText,
      "the serialized duplicate-email result must persist across restart",
    );
  } finally {
    await stopBackend(serverProcess);
    await rm(sandbox, { recursive: true, force: true });
  }

  console.log("backend integration QA passed: config, revocable auth, user validation and concurrency, risk evaluation, SSE delivery and revocation, and JSON persistence");
}

main().catch((error) => {
  console.error(`backend integration QA failed: ${error instanceof Error ? error.message : "unknown error"}`);
  process.exitCode = 1;
});
