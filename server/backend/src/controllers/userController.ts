import { Request, Response } from 'express';
import bcrypt from 'bcryptjs';
import { DuplicateEmailError, UserRepository } from '../repository/userRepository.js';
import { User, UserType } from '../models/user.js';

const userRepository = UserRepository.getInstance();

function invalidUpdate(res: Response, message: string): void {
  res.status(400).json({ code: 'invalid_user_update', message });
}

function normalizeEmail(email: string): string {
  return email.trim().toLowerCase();
}

export async function getUsers(req: Request, res: Response): Promise<void> {
  try {
    const users = await userRepository.getAll();
    // Exclude passwords from response
    const safeUsers = users.map(({ password, ...user }) => user);
    res.status(200).json(safeUsers);
  } catch (error) {
    console.error('Get users error:', error);
    res.status(500).json({ message: 'Internal server error' });
  }
}

export async function getUserById(req: Request, res: Response): Promise<void> {
  try {
    const { id } = req.params;
    const user = await userRepository.getById(id);

    if (!user) {
      res.status(404).json({ message: 'User not found' });
      return;
    }

    const { password, ...safeUser } = user;
    res.status(200).json(safeUser);
  } catch (error) {
    console.error('Get user error:', error);
    res.status(500).json({ message: 'Internal server error' });
  }
}

export async function updateUser(req: Request, res: Response): Promise<void> {
  try {
    const { id } = req.params;
    const body: unknown = req.body;
    if (typeof body !== 'object' || body === null || Array.isArray(body)) {
      invalidUpdate(res, 'Request body must be an object.');
      return;
    }

    const input = body as Record<string, unknown>;
    const allowedFields = ['email', 'password', 'userType'];
    if (
      Object.keys(input).some((field) => !allowedFields.includes(field)) ||
      !allowedFields.some((field) => Object.hasOwn(input, field))
    ) {
      invalidUpdate(res, 'Provide one or more supported user fields.');
      return;
    }

    let email: string | undefined;
    if (Object.hasOwn(input, 'email')) {
      if (typeof input.email !== 'string') {
        invalidUpdate(res, 'Email must be a valid email address.');
        return;
      }

      email = input.email.trim();
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
        invalidUpdate(res, 'Email must be a valid email address.');
        return;
      }
    }

    let password: string | undefined;
    if (Object.hasOwn(input, 'password')) {
      if (typeof input.password !== 'string' || input.password.length === 0) {
        invalidUpdate(res, 'Password must be a non-empty string.');
        return;
      }
      password = input.password;
    }

    let userType: UserType | undefined;
    if (Object.hasOwn(input, 'userType')) {
      if (input.userType !== 'user' && input.userType !== 'admin') {
        invalidUpdate(res, 'User type must be user or admin.');
        return;
      }
      userType = input.userType;
    }

    const user = await userRepository.getById(id);
    if (!user) {
      res.status(404).json({ message: 'User not found' });
      return;
    }

    const updates: Partial<Omit<User, 'id' | 'createdAt'>> = {};

    if (email !== undefined && normalizeEmail(email) !== normalizeEmail(user.email)) {
      updates.email = email;
    }

    if (password !== undefined) {
      updates.password = await bcrypt.hash(password, 10);
    }

    if (userType !== undefined) {
      updates.userType = userType;
    }

    const updatedUser = await userRepository.update(id, updates);
    if (!updatedUser) {
      res.status(500).json({ message: 'Failed to update user' });
      return;
    }

    const { password: _, ...safeUser } = updatedUser;
    res.status(200).json({
      message: 'User updated successfully',
      user: safeUser,
    });
  } catch (error) {
    if (error instanceof DuplicateEmailError) {
      res.status(400).json({
        code: 'email_already_in_use',
        message: 'Email already in use',
      });
      return;
    }
    console.error('Update user error:', error);
    res.status(500).json({ message: 'Internal server error' });
  }
}

export async function deleteUser(req: Request, res: Response): Promise<void> {
  try {
    const { id } = req.params;

    // Prevent administrators from deleting themselves if needed, or allow it
    // For simplicity, we just delete the user
    const deleted = await userRepository.delete(id);
    if (!deleted) {
      res.status(404).json({ message: 'User not found' });
      return;
    }

    res.status(200).json({ message: 'User deleted successfully' });
  } catch (error) {
    console.error('Delete user error:', error);
    res.status(500).json({ message: 'Internal server error' });
  }
}
