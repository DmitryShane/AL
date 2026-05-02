import { productivityClassName, productivityTone } from "../../utils/author";

type ProductivityCardProps = {
  value: number;
};

export function ProductivityCard({ value }: ProductivityCardProps) {
  return (
    <div className={`duration productivity-duration ${productivityTone(value)}`}>
      <span>Productivity</span>
      <strong className={productivityClassName(value)}>{value.toFixed(2)}%</strong>
    </div>
  );
}
