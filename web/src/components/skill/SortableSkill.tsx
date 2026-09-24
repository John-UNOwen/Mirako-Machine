import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Grip, X } from "lucide-react";

type Props = {
  id: string;
  position: number;
  onRemove: (id: string) => void;
};

// Separate from components/Sortable.tsx, which upper-cases its label and has no remove
// control -- it is shared with the action-sequence editor and should stay as it is.
export default function SortableSkill({ id, position, onRemove }: Props) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id });

  return (
    <div
      ref={setNodeRef}
      style={{
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.5 : 1,
      }}
      className="px-3 py-2 border-2 border-border rounded-lg flex gap-2 items-center bg-card"
    >
      {/* Dragging is confined to the handle so the remove button stays clickable. */}
      <button
        type="button"
        className="cursor-grab touch-none text-muted-foreground"
        aria-label={`Reorder ${id}`}
        {...attributes}
        {...listeners}
      >
        <Grip className="w-4 h-4" />
      </button>
      <span className="text-muted-foreground text-sm w-6">{position}.</span>
      <p className="grow text-sm break-words">{id}</p>
      <button
        type="button"
        aria-label={`Remove ${id}`}
        className="text-muted-foreground hover:text-destructive transition"
        onClick={() => onRemove(id)}
      >
        <X className="w-4 h-4" />
      </button>
    </div>
  );
}
