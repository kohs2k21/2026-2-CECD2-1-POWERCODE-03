import { Link } from "react-router-dom";
import { cn } from "@/lib/utils";

/** 단계 표시: 갈 수 있는 단계는 주소 이동 링크, 앞 단계가 끝나지 않은 단계는 비활성이다. */
export const StepLinks = ({ steps, step, maxStep, hrefFor }: { steps: string[]; step: number; maxStep: number; hrefFor: (step: number) => string }) => (
  <ol className="flex flex-wrap gap-1.5 text-sm">
    {steps.map((label, index) => {
      const number = index + 1;
      const current = number === step ? "step" : undefined;
      const look = cn("block rounded-md border px-2.5 py-1", current && "border-primary bg-primary text-primary-foreground", number < step && "border-success text-success-text");
      return (
        <li key={label}>
          {number <= maxStep
            ? <Link to={hrefFor(number)} aria-current={current} className={cn(look, "outline-none hover:bg-accent focus-visible:ring-3 focus-visible:ring-ring/50", current && "hover:bg-primary")}>{number}. {label}</Link>
            : <span aria-disabled="true" className={cn(look, "text-muted-foreground")}>{number}. {label}</span>}
        </li>
      );
    })}
  </ol>
);
