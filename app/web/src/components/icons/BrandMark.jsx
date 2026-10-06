import { cn } from "../../lib/utils";
import brandSvg from "../../assets/brand-mark.svg?raw";

/** The app's mark: a neutral bar-chart glyph (not a third-party logo). */
export function BrandMark({ className }) {
  return (
    <span
      aria-hidden="true"
      className={cn("inline-flex [&_svg]:h-full [&_svg]:w-full", className)}
      dangerouslySetInnerHTML={{ __html: brandSvg }}
    />
  );
}

export default BrandMark;
