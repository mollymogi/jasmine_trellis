// Copied from mollymogi/mogi-ui packages/ui/src/lib/utils.ts @ e1462bc.
import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
