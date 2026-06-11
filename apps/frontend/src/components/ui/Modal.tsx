import type { ReactNode } from "react";

export type ModalProps = {
  children: ReactNode;
  onBackdropClose: () => void;
  backdropDisabled?: boolean;
  /** Appended after base panel class `calendar-modal` (spacing-separated fragment allowed). */
  panelClassName?: string;
  ariaLabelledBy?: string;
  ariaDescribedBy?: string;
  "data-doc-target"?: string;
};

export function Modal({
  children,
  onBackdropClose,
  backdropDisabled = false,
  panelClassName = "",
  ariaLabelledBy,
  ariaDescribedBy,
  "data-doc-target": docTarget,
}: ModalProps) {
  const panelClasses = ["calendar-modal", panelClassName.trim()].filter(Boolean).join(" ");

  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onClick={() => {
        if (!backdropDisabled) {
          onBackdropClose();
        }
      }}
    >
      <div
        className={panelClasses}
        role="dialog"
        aria-modal="true"
        aria-labelledby={ariaLabelledBy}
        aria-describedby={ariaDescribedBy}
        data-doc-target={docTarget}
        onClick={(event) => event.stopPropagation()}
      >
        {children}
      </div>
    </div>
  );
}
