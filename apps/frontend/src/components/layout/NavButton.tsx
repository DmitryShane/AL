import type React from "react";

type NavButtonProps = {
  icon: React.ReactNode;
  label: string;
  active: boolean;
  onClick: () => void;
};

export function NavButton({ icon, label, active, onClick }: NavButtonProps) {
  return (
    <button className={active ? "side-nav-item active" : "side-nav-item"} onClick={onClick}>
      {icon}
      <span>{label}</span>
    </button>
  );
}
