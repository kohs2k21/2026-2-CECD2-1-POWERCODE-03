import { Request, Response, NextFunction } from 'express';
import jwt from 'jsonwebtoken';
import { JWT_SECRET } from '../config/jwt.js';
import { JWTPayload } from '../models/user.js';
import { UserRepository } from '../repository/userRepository.js';

const userRepository = UserRepository.getInstance();

export interface AuthenticatedRequest extends Request {
  user?: JWTPayload;
  tokenExpiresAt?: number;
}

function normalizeEmail(email: string): string {
  return email.trim().toLowerCase();
}

function rejectRevokedToken(res: Response): void {
  res.status(401).json({
    code: 'token_revoked',
    message: 'Access token is no longer valid',
  });
}

export function authenticateToken(
  req: AuthenticatedRequest,
  res: Response,
  next: NextFunction
): void {
  const authHeader = req.headers.authorization;
  const [scheme, token, ...extraParts] = authHeader?.trim().split(/\s+/) ?? [];

  if (scheme?.toLowerCase() !== 'bearer' || !token || extraParts.length > 0) {
    res.status(401).json({ message: 'Access token is missing or invalid' });
    return;
  }

  jwt.verify(token, JWT_SECRET, (err, decoded) => {
    if (err) {
      res.status(403).json({ message: 'Token is invalid or expired' });
      return;
    }

    if (
      typeof decoded !== 'object' ||
      decoded === null ||
      typeof decoded.id !== 'string' ||
      typeof decoded.email !== 'string' ||
      (decoded.userType !== 'user' && decoded.userType !== 'admin')
    ) {
      res.status(403).json({ message: 'Token is invalid or expired' });
      return;
    }

    const payload = decoded as JWTPayload & { exp?: number };
    void userRepository.getById(payload.id).then((user) => {
      if (
        !user ||
        normalizeEmail(user.email) !== normalizeEmail(payload.email) ||
        user.userType !== payload.userType
      ) {
        rejectRevokedToken(res);
        return;
      }

      req.user = {
        id: user.id,
        email: user.email,
        userType: user.userType,
      };
      req.tokenExpiresAt = typeof payload.exp === 'number' ? payload.exp * 1000 : undefined;
      next();
    }).catch(next);
  });
}

export function requireAdmin(
  req: AuthenticatedRequest,
  res: Response,
  next: NextFunction
): void {
  if (!req.user) {
    res.status(401).json({ message: 'Authentication required' });
    return;
  }

  if (req.user.userType !== 'admin') {
    res.status(403).json({ message: 'Admin privileges required' });
    return;
  }

  next();
}
