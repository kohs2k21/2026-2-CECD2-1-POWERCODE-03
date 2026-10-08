import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "../../../components/ui/button";
import { Modal } from "../../../components/ui/Modal";
import { ErrorState } from "../../../components/ui/feedback";
import { useAuthSession } from "../../../services/auth/AuthSessionProvider";
import { getStoredToken } from "../../../services/auth/session";
import {
  detectionQueryKey,
  useDetectionGateway,
} from "../data/useDetectionQuery";
import type { DetectionOperation } from "../data/types";
export const ServiceAction = ({
  operation,
  label,
  available,
  payload,
  disabled = false,
  requestDisabled = false,
  children,
}: {
  operation: DetectionOperation;
  label: string;
  available: boolean;
  payload: Record<string, unknown>;
  disabled?: boolean;
  requestDisabled?: boolean;
  children?: ReactNode;
}) => {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<string | null>(null);
  const gateway = useDetectionGateway();
  const { user } = useAuthSession();
  const cache = useQueryClient();
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), [user?.id]);
  const submit = async () => {
    if (pending || disabled || requestDisabled || receipt) return;
    if (!available) {
      setError(
        "작업 서비스에 연결되지 않아 요청할 수 없습니다. 편집 내용은 유지됩니다.",
      );
      return;
    }
    const requestToken = getStoredToken();
    const requestController = new AbortController();
    controller.current = requestController;
    setPending(true);
    setError(null);
    try {
      const result = await gateway.request(
        operation,
        payload,
        requestController.signal,
      );
      if (requestController.signal.aborted || getStoredToken() !== requestToken)
        return;
      setReceipt(
        `요청 접수: ${result.requestId}. 최종 처리 결과는 작업 상태에서 확인합니다.`,
      );
      await cache.invalidateQueries({ queryKey: detectionQueryKey(user!.id) });
    } catch (failure) {
      if (
        !requestController.signal.aborted &&
        getStoredToken() === requestToken
      )
        setError(
          failure instanceof Error
            ? failure.message
            : "요청에 실패했습니다. 편집 내용은 유지됩니다.",
        );
    } finally {
      if (
        !requestController.signal.aborted &&
        getStoredToken() === requestToken
      )
        setPending(false);
    }
  };
  return (
    <>
      <Button
        disabled={disabled}
        onClick={() => {
          setError(null);
          setReceipt(null);
          setOpen(true);
        }}
      >
        {label}
      </Button>
      <Modal
        isOpen={open}
        onOpenChange={(value) => {
          if (!pending) setOpen(value);
        }}
        title={`${label} 확인`}
        description="요청 내용과 현재 상태를 확인해 주세요."
        size="md"
      >
        {children}
        {!available && (
          <p role="status">
            작업 서비스에 연결되지 않아 요청할 수 없습니다. 편집 내용은
            유지됩니다.
          </p>
        )}
        {error && <ErrorState title={error} />}{" "}
        {receipt && <p role="status">{receipt}</p>}
        <div className="detection-actions">
          <Button
            variant="outline"
            disabled={pending}
            onClick={() => setOpen(false)}
          >
            취소
          </Button>
          <Button
            disabled={
              !available || requestDisabled || pending || Boolean(receipt)
            }
            onClick={() => {
              void submit();
            }}
          >
            {pending ? "요청 중..." : label}
          </Button>
        </div>
      </Modal>
    </>
  );
};
