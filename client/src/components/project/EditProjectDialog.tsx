"use client";

import { useEffect, useState } from "react";
import { Pencil } from "lucide-react";
import { fetchFromApi } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { useProjectWorkspace } from "@/components/project/ProjectWorkspaceProvider";
import { useI18n } from "@/lib/i18n";

interface EditProjectDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
}

// Edit the project's title + description. Lives in the workspace header so
// it's reachable from every project route.
export function EditProjectDialog({ open, onOpenChange }: EditProjectDialogProps) {
    const { t } = useI18n();
    const { project, refetchProject } = useProjectWorkspace();
    const [currentTitle, setCurrentTitle] = useState("");
    const [currentDescription, setCurrentDescription] = useState("");

    // Seed the fields from the project each time the dialog opens.
    useEffect(() => {
        if (open && project) {
            setCurrentTitle(project.title);
            setCurrentDescription(project.description || "");
        }
    }, [open, project]);

    const handleUpdateProject = async () => {
        if (!project) return;
        try {
            const response = await fetchFromApi(`/api/projects/${project.id}`, {
                method: "PATCH",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({
                    title: currentTitle,
                    description: currentDescription,
                }),
            });
            if (response) {
                refetchProject();
                onOpenChange(false);
            } else {
                console.error("Failed to update project");
            }
        } catch (error) {
            console.error("An error occurred while updating the project:", error);
        }
    };

    return (
        <AlertDialog open={open} onOpenChange={onOpenChange}>
            <AlertDialogContent>
                <AlertDialogHeader>
                    <AlertDialogTitle>{t("Edit Project")}</AlertDialogTitle>
                    <AlertDialogDescription>
                        {t("Update the title and description for your project.")}
                    </AlertDialogDescription>
                </AlertDialogHeader>
                <div className="grid gap-4 py-4">
                    <div className="grid grid-cols-4 items-center gap-4">
                        <Label htmlFor="title" className="text-right">
                            {t("Title")}
                        </Label>
                        <Input
                            id="title"
                            value={currentTitle}
                            onChange={(e) => setCurrentTitle(e.target.value)}
                            className="col-span-3"
                        />
                    </div>
                    <div className="grid grid-cols-4 items-center gap-4">
                        <Label htmlFor="description" className="text-right">
                            {t("Description")}
                        </Label>
                        <Textarea
                            id="description"
                            value={currentDescription}
                            onChange={(e) => setCurrentDescription(e.target.value)}
                            className="col-span-3"
                        />
                    </div>
                </div>
                <AlertDialogFooter>
                    <AlertDialogCancel>{t("Cancel")}</AlertDialogCancel>
                    <AlertDialogAction onClick={handleUpdateProject}>{t("Save")}</AlertDialogAction>
                </AlertDialogFooter>
            </AlertDialogContent>
        </AlertDialog>
    );
}

// Convenience: dialog + its trigger state, rendered as a hover-revealed pencil.
export function EditProjectButton({ className }: { className?: string }) {
    const { t } = useI18n();
    const [open, setOpen] = useState(false);
    return (
        <>
            <button
                onClick={() => setOpen(true)}
                className={className}
                aria-label={t("Edit project title and description")}
                title={t("Edit project")}
            >
                <Pencil className="h-3 w-3" />
            </button>
            <EditProjectDialog open={open} onOpenChange={setOpen} />
        </>
    );
}
