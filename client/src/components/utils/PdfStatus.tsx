import React from "react";
import { CircleDashed, CheckCircle, CircleDot } from "lucide-react";

// Enum for Paper statuses
export type PaperStatus = "todo" | "reading" | "completed";

// Export as an Enum
export const PaperStatusEnum = {
    TODO: "todo",
    READING: "reading",
    COMPLETED: "completed"
} as const;

export const getStatusIcon = (status: PaperStatus) => {
    if (status === PaperStatusEnum.TODO) {
        return <CircleDashed size={ 16 } className = "text-yellow-500" />;
    }
    if (status === PaperStatusEnum.READING) {
        return <CircleDot size={ 16 } className = "text-blue-500" />;
    }
    if (status === PaperStatusEnum.COMPLETED) {
        return <CheckCircle size={ 16 } className = "text-green-500" />;
    }
    return null;
};

/** Chinese labels are deliberately two characters each: 待读 / 在读 / 已读. */
export const getStatusLabel = (
    status: PaperStatus | string,
    t: (text: string) => string,
): string => {
    if (status === PaperStatusEnum.TODO) return t("Todo");
    if (status === PaperStatusEnum.READING) return t("Reading");
    if (status === PaperStatusEnum.COMPLETED) return t("Completed");
    return status;
};
