// React 19's types no longer declare the global `JSX` namespace. The dashboard's views and
// components annotate return types as `JSX.Element`, so this keeps that spelling working.
import type { JSX as ReactJSX } from 'react'

declare global {
  namespace JSX {
    type Element = ReactJSX.Element
  }
}
