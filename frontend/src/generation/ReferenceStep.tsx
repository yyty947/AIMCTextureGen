import { useId } from "react";

import type {
  PackReferenceRecord,
  ReferenceSelection,
  UploadedReferenceRecord,
} from "./types";

export default function ReferenceStep({
  packReferences,
  styleUploads,
  structureUploads,
  selectedStyleReferences,
  selectedStructureReference,
  userDescription,
  onToggleStyleReference,
  onStructureReferenceChange,
  onUserDescriptionChange,
  onUpload,
  onDelete,
  uploadingKind,
  error,
  onBack,
  onNext,
}: {
  readonly packReferences: readonly PackReferenceRecord[];
  readonly styleUploads: readonly UploadedReferenceRecord[];
  readonly structureUploads: readonly UploadedReferenceRecord[];
  readonly selectedStyleReferences: readonly ReferenceSelection[];
  readonly selectedStructureReference: ReferenceSelection | null;
  readonly userDescription: string;
  readonly onToggleStyleReference: (selection: ReferenceSelection) => void;
  readonly onStructureReferenceChange: (selection: ReferenceSelection | null) => void;
  readonly onUserDescriptionChange: (value: string) => void;
  readonly onUpload: (kind: "style" | "structure", file: File) => void;
  readonly onDelete: (kind: "style" | "structure", referenceId: string) => void;
  readonly uploadingKind: "style" | "structure" | null;
  readonly error: string | null;
  readonly onBack: () => void;
  readonly onNext: () => void;
}) {
  const structureSelectId = useId();
  return (
    <section className="panel generation-panel" aria-labelledby="generation-reference-title">
      <div className="section-heading">
        <span className="step-marker" aria-hidden="true">03</span>
        <div>
          <h2 id="generation-reference-title">参考图与描述</h2>
          <p>支持 0–8 张风格参考与 0 或 1 张结构参考。</p>
        </div>
      </div>

      <div className="field">
        <p>风格参考（0–8 张）</p>
        <div className="choice-grid">
          {packReferences.map((reference) => {
            const selection: ReferenceSelection = {
              source: "pack",
              relativePath: reference.relativePath,
            };
            const checked = selectedStyleReferences.some(
              (item) =>
                item.source === "pack" &&
                item.relativePath === reference.relativePath,
            );
            return (
              <label className="choice-card" key={`pack:${reference.relativePath}`}>
                <input
                  checked={checked}
                  type="checkbox"
                  onChange={() => onToggleStyleReference(selection)}
                />
                <span>{reference.displayName}</span>
              </label>
            );
          })}
          {styleUploads.map((reference) => {
            const selection: ReferenceSelection = {
              source: "upload",
              referenceId: reference.referenceId,
            };
            const checked = selectedStyleReferences.some(
              (item) =>
                item.source === "upload" &&
                item.referenceId === reference.referenceId,
            );
            return (
              <div className="choice-card" key={`upload:${reference.referenceId}`}>
                <label>
                  <input
                    checked={checked}
                    type="checkbox"
                    onChange={() => onToggleStyleReference(selection)}
                  />
                  <span>{reference.referenceId}</span>
                </label>
                <button
                  aria-label={`删除风格参考 ${reference.referenceId}`}
                  disabled={uploadingKind !== null}
                  type="button"
                  onClick={() => onDelete("style", reference.referenceId)}
                >
                  删除
                </button>
              </div>
            );
          })}
        </div>
        <label htmlFor="style-reference-upload">上传风格参考</label>
        <input
          id="style-reference-upload"
          accept="image/png"
          aria-label="上传风格参考"
          disabled={uploadingKind !== null}
          type="file"
          onChange={(event) => {
            const file = event.currentTarget.files?.item(0);
            event.currentTarget.value = "";
            if (file !== null && file !== undefined) {
              onUpload("style", file);
            }
          }}
        />
      </div>

      <div className="field">
        <label htmlFor={structureSelectId}>结构参考</label>
        <select
          aria-label="结构参考"
          id={structureSelectId}
          value={
            selectedStructureReference?.source === "upload"
              ? selectedStructureReference.referenceId
              : ""
          }
          onChange={(event) => {
            const value = event.currentTarget.value;
            onStructureReferenceChange(
              value === "" ? null : { source: "upload", referenceId: value },
            );
          }}
        >
          <option value="">不使用结构参考</option>
          {structureUploads.map((reference) => (
            <option key={reference.referenceId} value={reference.referenceId}>
              {reference.referenceId}
            </option>
          ))}
        </select>
        <p>可选结构参考</p>
        <label htmlFor="structure-reference-upload">上传结构参考</label>
        <input
          id="structure-reference-upload"
          accept="image/png"
          aria-label="上传结构参考"
          disabled={uploadingKind !== null}
          type="file"
          onChange={(event) => {
            const file = event.currentTarget.files?.item(0);
            event.currentTarget.value = "";
            if (file !== null && file !== undefined) {
              onUpload("structure", file);
            }
          }}
        />
        {structureUploads.map((reference) => (
          <button
            aria-label={`删除结构参考 ${reference.referenceId}`}
            disabled={uploadingKind !== null}
            key={`delete-structure:${reference.referenceId}`}
            type="button"
            onClick={() => onDelete("structure", reference.referenceId)}
          >
            删除结构参考 {reference.referenceId}
          </button>
        ))}
      </div>

      <div className="field">
        <label htmlFor="generation-description">补充描述</label>
        <textarea
          id="generation-description"
          value={userDescription}
          onChange={(event) => onUserDescriptionChange(event.currentTarget.value)}
        />
      </div>

      {error !== null && <p className="validation-error" role="alert">{error}</p>}

      <div className="wizard-actions">
        <button type="button" onClick={onBack}>
          上一步：选择目标
        </button>
        <button type="button" onClick={onNext}>
          下一步：生成配置
        </button>
      </div>
    </section>
  );
}
