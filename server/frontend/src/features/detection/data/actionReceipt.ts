import type { ActionReceipt } from "./types";

export const taskDetailHref = (job: NonNullable<ActionReceipt["job"]>) => {
  if (typeof job.id !== "string" || !job.id.trim())
    return "/notifications?filter=tasks";
  if (job.type === "training")
    return (
      "/detection/create?" +
      new URLSearchParams({ tab: "training", job: job.id })
    );
  if (
    job.type === "evaluation" &&
    typeof job.resultId === "string" &&
    job.resultId.trim()
  )
    return (
      "/detection/evaluation?" +
      new URLSearchParams({
        tab: "candidates",
        result: job.resultId,
        ...(job.candidateId ? { candidate: job.candidateId } : {}),
      })
    );
  return "/notifications?filter=tasks";
};
export const receiptTaskHref = (receipt: ActionReceipt) =>
  receipt.job ? taskDetailHref(receipt.job) : "/notifications?filter=tasks";
