import { motion, useReducedMotion } from "motion/react";
import type { ReactNode } from "react";

type AnimatedPanelProps = {
  children: ReactNode;
  className?: string;
};

export const AnimatedPanel = ({ children, className }: AnimatedPanelProps) => {
  const reduceMotion = useReducedMotion();
  return (
    <motion.div
      animate={{ opacity: 1, scale: 1, y: 0 }}
      className={className}
      exit={reduceMotion ? { opacity: 0 } : { opacity: 0, scale: 0.995, y: 10 }}
      initial={reduceMotion ? false : { opacity: 0, scale: 0.995, y: 10 }}
      transition={{ duration: reduceMotion ? 0 : 0.22, ease: "easeOut" }}
    >
      {children}
    </motion.div>
  );
};
