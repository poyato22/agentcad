import type { ComponentChildren, JSX } from "preact";

/**
 * default = outlined; primary = blue fill (only for the action that needs the human, e.g. Merge);
 * ink = white fill; danger = red text (stop/fail); ghost = borderless.
 */
export type ButtonVariant = "default" | "primary" | "ink" | "danger" | "ghost";

export type ButtonProps = Omit<JSX.ButtonHTMLAttributes<HTMLButtonElement>, "class" | "className"> & {
  variant?: ButtonVariant;
  class?: string;
  children?: ComponentChildren;
};

export function Button({ variant = "default", class: cls, type = "button", children, ...rest }: ButtonProps): JSX.Element {
  const classes = ["btn", variant !== "default" ? `btn-${variant}` : "", cls ?? ""].filter(Boolean).join(" ");
  return <button type={type} class={classes} {...rest}>{children}</button>;
}
