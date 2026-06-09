import { productivityClassName, productivityTone } from "../../utils/author";

type ProductivityCardProps = {
  value: number;
};

export function ProductivityCard({ value }: ProductivityCardProps) {
  const productivity = Number.isFinite(value) ? value : 0;

  return (
    <div className={`duration productivity-duration ${productivityTone(productivity)}`}>
      <span>Productivity</span>
      <strong className={productivityClassName(productivity)}>{productivity.toFixed(2)}%</strong>
    </div>
  );
}
