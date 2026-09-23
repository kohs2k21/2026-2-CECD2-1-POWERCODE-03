import { Router } from 'express';
import { login, me, registrationDisabled } from '../controllers/authController.js';
import { authenticateToken } from '../middleware/auth.js';

const router = Router();

// Public login is the only enabled auth operation. Account creation is local seed configuration.
router.post('/send-code', registrationDisabled);
router.post('/verify-code', registrationDisabled);
router.post('/register', registrationDisabled);
router.post('/login', login);

// Protected routes
router.get('/me', authenticateToken as any, me as any);

export default router;
