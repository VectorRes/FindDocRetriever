import { useRef, useState } from "react";
import type { DragEvent } from "react";
import { ApiError, uploadDocument } from "../api/client";
import type { DocumentOut } from "../api/types";

const ALLOWED_EXTENSIONS = [".xlsx", ".xlsm", ".pdf"];

interface DocumentUploadProps {
  onUploaded: (document: DocumentOut) => void;
}

type UploadState = "idle" | "uploading" | "error";

function hasAllowedExtension(filename: string): boolean {
  const lower = filename.toLowerCase();
  return ALLOWED_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

export default function DocumentUpload({ onUploaded }: DocumentUploadProps) {
  const [state, setState] = useState<UploadState>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  async function submit(file: File) {
    if (!hasAllowedExtension(file.name)) {
      setPendingFile(null);
      setErrorMessage(`Unsupported file type. Allowed: ${ALLOWED_EXTENSIONS.join(", ")}`);
      setState("error");
      return;
    }

    setPendingFile(file);
    setState("uploading");
    setErrorMessage(null);

    try {
      const document = await uploadDocument(file);
      setState("idle");
      setPendingFile(null);
      onUploaded(document);
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : "Upload failed.");
      setState("error");
    }
  }

  function handleFileInput(files: FileList | null) {
    const file = files?.[0];
    if (file) void submit(file);
  }

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragOver(false);
    handleFileInput(e.dataTransfer.files);
  }

  function retry() {
    if (pendingFile) void submit(pendingFile);
  }

  return (
    <div>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setIsDragOver(true);
        }}
        onDragLeave={() => setIsDragOver(false)}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        aria-label="Upload a financial document"
        style={{
          ...dropzoneStyle,
          borderColor: isDragOver ? "#4f46e5" : "#cbd5e1",
          background: isDragOver ? "#eef2ff" : "#f8fafc",
          cursor: state === "uploading" ? "not-allowed" : "pointer",
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept={ALLOWED_EXTENSIONS.join(",")}
          onChange={(e) => handleFileInput(e.target.files)}
          disabled={state === "uploading"}
          style={{ display: "none" }}
        />
        {state === "uploading" ? (
          <span style={{ color: "#4f46e5" }}>Uploading and processing {pendingFile?.name}...</span>
        ) : (
          <span style={{ color: "#64748b" }}>
            Drag & drop an Excel or PDF file here, or click to browse
            <br />
            <span style={{ fontSize: "0.75rem" }}>Allowed: {ALLOWED_EXTENSIONS.join(", ")}</span>
          </span>
        )}
      </div>

      {state === "error" && (
        <div style={errorBannerStyle}>
          <span>{errorMessage}</span>
          {pendingFile && hasAllowedExtension(pendingFile.name) && (
            <button type="button" onClick={retry} style={retryButtonStyle}>
              Retry
            </button>
          )}
        </div>
      )}
    </div>
  );
}

const dropzoneStyle = {
  border: "2px dashed #cbd5e1",
  borderRadius: "0.6rem",
  padding: "1.5rem",
  textAlign: "center" as const,
  fontSize: "0.9rem",
};

const errorBannerStyle = {
  marginTop: "0.5rem",
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  gap: "0.75rem",
  padding: "0.6rem 0.8rem",
  borderRadius: "0.5rem",
  background: "#fef2f2",
  color: "#b91c1c",
  fontSize: "0.85rem",
};

const retryButtonStyle = {
  padding: "0.3rem 0.7rem",
  borderRadius: "0.4rem",
  border: "1px solid #b91c1c",
  background: "white",
  color: "#b91c1c",
  cursor: "pointer",
  fontSize: "0.8rem",
  flexShrink: 0,
};
