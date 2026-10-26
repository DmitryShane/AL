import { useState } from "react";
import type { PublisherAuthorsController } from "./usePublisherAuthors";

export function AuthorPublisherAssignment({ author, controller, disabled }: { author: string; controller: PublisherAuthorsController; disabled: boolean }) {
  const [publisher, setPublisher] = useState("");
  return <div className="publisher-author-assignment">
    <select aria-label={`Publisher for ${author}`} value={publisher} disabled={disabled || controller.busy} onChange={e => setPublisher(e.target.value)}>
      <option value="">Select publisher</option>
      {controller.profiles.map(p => <option key={p.rawAuthor} value={p.rawAuthor}>{p.displayName}</option>)}
    </select>
    <button type="button" disabled={disabled || controller.busy || !publisher} onClick={() => void controller.assign(publisher, author, true)}>Assign</button>
  </div>;
}
