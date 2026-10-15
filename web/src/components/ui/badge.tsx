// Copied from mollymogi/mogi-ui packages/ui/src/components/ui/badge.tsx @ 43416b3 (read-only source).
import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"
import { Slot } from "radix-ui"

const badgeVariants = cva(
  // mogi: 中文在 12px 下容易显得挤，加大内边距、图标间距和字距；带前置图标时左侧略收
  "inline-flex w-fit shrink-0 items-center justify-center gap-1.5 overflow-hidden rounded-sm border border-transparent px-2.5 py-1 text-xs font-medium tracking-wide whitespace-nowrap has-[>svg:first-child]:pl-2 transition-[color,box-shadow] focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 [&>svg]:pointer-events-none [&>svg]:size-3.5",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground [a&]:hover:bg-primary/90",
        secondary:
          "bg-secondary text-secondary-foreground [a&]:hover:bg-secondary/90",
        destructive:
          "bg-destructive text-white focus-visible:ring-destructive/20 dark:bg-destructive/60 dark:focus-visible:ring-destructive/40 [a&]:hover:bg-destructive/90",
        outline:
          "border-border text-foreground [a&]:hover:bg-accent [a&]:hover:text-accent-foreground",
        ghost: "[a&]:hover:bg-accent [a&]:hover:text-accent-foreground",
        // mogi: 低饱和底色 + 深色文字的柔和标签
        soft: "bg-accent text-accent-foreground [a&]:hover:bg-accent/80",
        success: "bg-success-soft text-success [a&]:hover:bg-success-soft/80",
        warning:
          "bg-warning-soft text-warning-foreground dark:text-warning [a&]:hover:bg-warning-soft/80",
        highlight: "bg-highlight-soft text-highlight [a&]:hover:bg-highlight-soft/80",
        link: "text-primary underline-offset-4 [a&]:hover:underline",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

function Badge({
  className,
  variant = "default",
  asChild = false,
  ...props
}: React.ComponentProps<"span"> &
  VariantProps<typeof badgeVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot.Root : "span"

  return (
    <Comp
      data-slot="badge"
      data-variant={variant}
      className={cn(badgeVariants({ variant }), className)}
      {...props}
    />
  )
}

export { Badge, badgeVariants }
