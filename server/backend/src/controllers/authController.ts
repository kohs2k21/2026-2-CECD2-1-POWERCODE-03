import { Request, Response } from "express";
import jwt from "jsonwebtoken";
import bcrypt from "bcryptjs";
import { UserRepository } from "../repository/userRepository.js";
import { JWT_EXPIRES_IN, JWT_SECRET } from "../config/jwt.js";
import { AuthenticatedRequest } from "../middleware/auth.js";
import { JWTPayload } from "../models/user.js";

const userRepository = UserRepository.getInstance();

export function registrationDisabled(_req: Request, res: Response): void {
  res.status(410).json({
    code: "registration_disabled",
    message: "Public registration and email verification are disabled.",
  });
}

// Keep the old controller exports as disabled handlers for callers that still import them.
export const sendVerificationCode = registrationDisabled;
export const verifyCode = registrationDisabled;
export const register = registrationDisabled;

export async function login(req: Request, res: Response): Promise<void> {
  try {
    const email = typeof req.body?.email === "string" ? req.body.email : "";
    const password = typeof req.body?.password === "string" ? req.body.password : "";

    if (!email || !password) {
      res.status(400).json({ message: "Email and password are required" });
      return;
    }

    const user = await userRepository.getByEmail(email);
    if (!user || !user.password) {
      res.status(401).json({ message: "Invalid email or password" });
      return;
    }

    const isPasswordValid = await bcrypt.compare(password, user.password);
    if (!isPasswordValid) {
      res.status(401).json({ message: "Invalid email or password" });
      return;
    }

    const payload: JWTPayload = {
      id: user.id,
      email: user.email,
      userType: user.userType,
    };
    const token = jwt.sign(payload, JWT_SECRET, {
      expiresIn: JWT_EXPIRES_IN as any,
    });

    res.status(200).json({
      message: "Login successful",
      token,
      user: {
        id: user.id,
        email: user.email,
        userType: user.userType,
        createdAt: user.createdAt,
      },
    });
  } catch (error) {
    console.error("Login error:", error instanceof Error ? error.message : "unknown error");
    res.status(500).json({ message: "Internal server error" });
  }
}

export async function me(req: AuthenticatedRequest, res: Response): Promise<void> {
  try {
    if (!req.user) {
      res.status(401).json({ message: "Unauthorized" });
      return;
    }

    const user = await userRepository.getById(req.user.id);
    if (!user) {
      res.status(404).json({ message: "User not found" });
      return;
    }

    res.status(200).json({
      user: {
        id: user.id,
        email: user.email,
        userType: user.userType,
        createdAt: user.createdAt,
      },
    });
  } catch (error) {
    console.error("Fetch profile error:", error instanceof Error ? error.message : "unknown error");
    res.status(500).json({ message: "Internal server error" });
  }
}
