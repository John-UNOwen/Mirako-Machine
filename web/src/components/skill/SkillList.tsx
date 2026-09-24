import { Ban, X } from "lucide-react";
import { Input } from "../ui/input";
import { useMemo, useState } from "react";
import {
  closestCenter,
  DndContext,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  arrayMove,
  SortableContext,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import SortableSkill from "./SortableSkill";
import { useQuery } from "@tanstack/react-query";
import type { SkillData } from "@/types/skill.type";

import Tooltips from "@/components/_c/Tooltips";

type Props = {
  list: string[];
  blacklist: string[];
  addSkillList: (newList: string) => void;
  deleteSkillList: (newList: string) => void;
  addBlacklist: (name: string) => void;
  deleteBlacklist: (name: string) => void;
  reorderSkillList: (newList: string[]) => void;
};

// The blacklist is matched by family at runtime, so banning any one tier bans them all.
// Stripping the glyph here keeps the picker honest about that: a banned family vanishes
// from the catalogue entirely rather than leaving its other tiers looking available.
const TIER_GLYPHS = ["○", "◎", "×", "☆"];
const familyOf = (name: string) => {
  const trimmed = name.trimEnd();
  const last = trimmed.slice(-1);
  return TIER_GLYPHS.includes(last) ? trimmed.slice(0, -1).trimEnd() : trimmed;
};

export default function SkillList({
  list,
  blacklist,
  addSkillList,
  deleteSkillList,
  addBlacklist,
  deleteBlacklist,
  reorderSkillList,
}: Props) {
  const [search, setSearch] = useState("");
  const bannedFamilies = useMemo(
    () => new Set(blacklist.map(familyOf)),
    [blacklist]
  );
  // A small activation distance keeps a click on the remove button from starting a drag.
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } })
  );

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const from = list.indexOf(String(active.id));
    const to = list.indexOf(String(over.id));
    if (from === -1 || to === -1) return;
    reorderSkillList(arrayMove(list, from, to));
  };

  const getSkillData = async () => {
    try {
      const res = await fetch("/data/skills.json");
      if (!res.ok) throw new Error("Failed to fetch skills");
      return res.json();
    } catch (error) {
      console.error("Failed to fetch skills:", error);
    }
  };

  const { data } = useQuery<SkillData[]>({
    queryKey: ["skills"],
    queryFn: getSkillData,
    staleTime: 0,
  });

  const filtered = useMemo(() => {
    return data?.filter(
      (skill) =>
        skill.name.toLowerCase().includes(search.toLowerCase()) ||
        skill.description.toLowerCase().includes(search.toLowerCase())
    );
  }, [data, search]);

  const handleSearch = (e: React.ChangeEvent<HTMLInputElement>) => {
    setSearch(e.target.value);
  };

  return (
    <>
      <div className="flex items-center gap-2">
        <p className="text-lg font-medium mb-2">Select skills you want to buy</p>
        <Tooltips>Order is the priority order, highest first, and only Independent Training reads it -- a normal career treats this as an unordered list. Independent Training does tell "◎", "○" and "×" apart: OCR mangles those glyphs, but the names are resolved against the game's own skill data afterwards. A normal career cannot, and will buy whichever tier it meets first.</Tooltips>
      </div>
          <div className="flex gap-6 min-h-[400px]">
            {/* LEFT SIDE */}
            <div className="w-9/12 flex flex-col">
              <Input
                placeholder="Search..."
                type="search"
                value={search}
                onChange={handleSearch}
              />

              <div className="mt-4 grid grid-cols-2 gap-4 overflow-auto pr-2 max-h-[calc(80vh-11rem)]">
                {filtered?.map(
                  (skill) =>
                    !list.includes(skill.name) &&
                    !bannedFamilies.has(familyOf(skill.name)) && (
                      <div
                        key={skill.name}
                        className="w-full border-2 border-border rounded-lg px-3 py-2 cursor-pointer hover:border-primary/50 transition"
                        onClick={() => addSkillList(skill.name)}
                      >
                        <div className="flex items-center gap-2">
                          <img
                            src={`assets/icons/${skill.iconid}.png`}
                            alt=""
                            className="w-6 h-6"
                          />
                          <span className="text-lg font-semibold grow">{skill.name}</span>
                          <button
                            type="button"
                            title="Never buy this skill"
                            aria-label={`Never buy ${skill.name}`}
                            className="shrink-0 text-muted-foreground hover:text-destructive transition"
                            onClick={(e) => {
                              e.stopPropagation();
                              addBlacklist(skill.name);
                            }}
                          >
                            <Ban className="w-4 h-4" />
                          </button>
                        </div>
                        <p className="text-sm text-muted-foreground">
                          {skill.description}
                        </p>
                      </div>
                    )
                )}
              </div>
            </div>

            {/* RIGHT SIDE */}
            <div className="w-3/12 flex flex-col">
              <p className="font-semibold mb-2">Prioritised skills</p>
              <p className="text-xs text-muted-foreground mb-2">
                Drag by the handle to reorder. Highest priority first. Leaving this empty
                is fine &mdash; with leftover spending on, the whole balance goes to the
                leftover strategy.
              </p>
              <DndContext
                sensors={sensors}
                collisionDetection={closestCenter}
                onDragEnd={handleDragEnd}
              >
                <SortableContext items={list} strategy={verticalListSortingStrategy}>
                  <div className="flex flex-col gap-2 overflow-auto pr-2 max-h-[calc(45vh-8rem)]">
                    {list.map((item, index) => (
                      <SortableSkill
                        key={item}
                        id={item}
                        position={index + 1}
                        onRemove={deleteSkillList}
                      />
                    ))}
                  </div>
                </SortableContext>
              </DndContext>

              <div className="flex items-center gap-2 mt-6 mb-2">
                <Ban className="w-4 h-4 text-destructive" />
                <p className="font-semibold">Never buy</p>
                <Tooltips>
                  Vetoes a skill outright: it will not be bought to fill the priority
                  list, and not bought with leftover points either. Blocked by family, so
                  banning any one tier bans "◎", "○" and "×" alike &mdash; which is why a
                  banned skill disappears from the catalogue completely. A skill cannot be
                  in both lists; moving it here takes it out of the buy list.
                </Tooltips>
              </div>
              {blacklist.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  Nothing banned. Use the
                  <Ban className="w-3 h-3 inline mx-1" />
                  on a skill to add it here.
                </p>
              ) : (
                <div className="flex flex-col gap-2 overflow-auto pr-2 max-h-[calc(35vh-8rem)]">
                  {blacklist.map((item) => (
                    <div
                      key={item}
                      className="px-3 py-2 border-2 border-destructive/40 bg-destructive/5 rounded-lg flex gap-2 items-center"
                    >
                      <span className="grow text-sm break-all">{item}</span>
                      <button
                        type="button"
                        aria-label={`Stop banning ${item}`}
                        className="text-muted-foreground hover:text-foreground transition"
                        onClick={() => deleteBlacklist(item)}
                      >
                        <X className="w-4 h-4" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

    </>
  );
}
