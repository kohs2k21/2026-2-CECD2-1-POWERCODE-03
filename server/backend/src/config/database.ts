import { Pool } from 'pg';
import dotenv from 'dotenv';

dotenv.config();

let pool: Pool | undefined;

export function getDatabasePool(): Pool {
  const databaseUrl = process.env.DATABASE_URL?.trim();
  if (!databaseUrl) {
    throw new Error('DATABASE_URL is required for PostgreSQL operations');
  }

  if (!pool) {
    pool = new Pool({
      connectionString: databaseUrl,
      max: 10,
      connectionTimeoutMillis: 5000,
      idleTimeoutMillis: 30000,
    });
  }
  return pool;
}

export async function checkDatabaseConnection(): Promise<boolean> {
  try {
    await getDatabasePool().query('SELECT 1');
    return true;
  } catch {
    // 연결 오류에는 인증 정보나 네트워크 상세 정보가 포함될 수 있으므로 노출하지 않는다.
    return false;
  }
}
