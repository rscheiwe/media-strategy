import { cn } from "../../lib/utils";
import loaderSvg from "../../assets/loader-icon.svg?raw";

const LOADER_STYLE = `
  <style>
    .loader-spoke {
      fill: var(--loader-idle, #d9d9d9);
      animation: loader-blink var(--loader-duration, 1000ms) linear infinite;
    }

    .loader-spoke:nth-of-type(1) { animation-delay: 750ms; }
    .loader-spoke:nth-of-type(2) { animation-delay: 500ms; }
    .loader-spoke:nth-of-type(3) { animation-delay: 250ms; }
    .loader-spoke:nth-of-type(4) { animation-delay: 125ms; }
    .loader-spoke:nth-of-type(5) { animation-delay: 625ms; }
    .loader-spoke:nth-of-type(6) { animation-delay: 375ms; }
    .loader-spoke:nth-of-type(7) { animation-delay: 875ms; }
    .loader-spoke:nth-of-type(8) { animation-delay: 0ms; }

    @keyframes loader-blink {
      0%, 100% {
        fill: var(--loader-idle, #d9d9d9);
      }

      12.5% {
        fill: var(--loader-active, #000000);
      }
    }
  </style>
`;

const animatedLoaderSvg = loaderSvg
  .replace(/<svg\b([^>]*)>/, `<svg$1>${LOADER_STYLE}`)
  .replace(
    /<path fill="#000000" opacity="1\.000000" stroke="none"/g,
    '<path class="loader-spoke" stroke="none"',
  );

export function LoaderIcon({
  className,
  idleColor = "#d9d9d9",
  activeColor = "#000000",
  durationMs = 1000,
}) {
  return (
    <span
      aria-hidden="true"
      className={cn("inline-flex [&_svg]:h-full [&_svg]:w-full", className)}
      style={{
        "--loader-idle": idleColor,
        "--loader-active": activeColor,
        "--loader-duration": `${durationMs}ms`,
      }}
      dangerouslySetInnerHTML={{ __html: animatedLoaderSvg }}
    />
  );
}

export default LoaderIcon;
