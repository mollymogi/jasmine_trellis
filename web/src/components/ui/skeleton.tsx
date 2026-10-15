// Copied from mollymogi/mogi-ui packages/ui/src/components/ui/skeleton.tsx @ e1462bc (read-only source; see UPSTREAM.md in mogi-ui).
import { cn } from "@/lib/utils"

function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      className={cn("animate-pulse rounded-md bg-accent", className)}
      {...props}
    />
  )
}

export { Skeleton }
