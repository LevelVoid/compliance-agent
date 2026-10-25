import { Router } from "express";
import { requireRole } from "../middleware/auth";

const router = Router();

router.get("/admin/users", requireRole("admin"), async (req, res) => {
  const users = await req.db.collection("users").find({}).toArray();
  res.json(users);
});

router.get("/admin/refunds", requireRole("admin"), async (req, res) => {
  const refunds = await req.db.collection("refunds").find({}).toArray();
  res.json(refunds);
});

router.get("/health", (req, res) => res.json({ ok: true }));

export default router;
