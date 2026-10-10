import toast from "react-hot-toast";

export const notificationDurations = {
  success: 2400,
  error: 6000,
  loading: Infinity,
} as const;
export const notify = {
  success(message: string, id = "success:" + message) {
    return toast.success(message, {
      id,
      duration: notificationDurations.success,
      ariaProps: { role: "status", "aria-live": "polite" },
    });
  },
  error(message: string, id = "error:" + message) {
    return toast.error(message, {
      id,
      duration: notificationDurations.error,
      ariaProps: { role: "alert", "aria-live": "assertive" },
    });
  },
  loading(message: string, id: string) {
    return toast.loading(message, {
      id,
      duration: notificationDurations.loading,
      ariaProps: { role: "status", "aria-live": "polite" },
    });
  },
  dismiss(id: string) {
    toast.dismiss(id);
  },
  clear() {
    toast.remove();
  },
};
