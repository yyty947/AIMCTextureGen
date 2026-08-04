import { useEffect, useMemo, useState } from "react";

import type { CoverageReport, ProjectManifest } from "../api";
import type { JobDetail } from "../api";
import {
  createGenerationJob,
  getGenerationOptions,
  listPackReferences,
  listUploadedReferences,
  uploadReference,
  deleteUploadedReference,
  startGenerationJob,
} from "./api";
import ReferenceStep from "./ReferenceStep";
import TargetStep from "./TargetStep";
import GenerationStep from "./GenerationStep";
import type {
  GenerationOptions,
  ReferenceSelection,
} from "./types";

export default function GenerationWizard({
  projectId,
  manifest,
  coverage,
  onJobsChanged,
  onCurrentJobChange,
}: {
  readonly projectId: string;
  readonly manifest: ProjectManifest;
  readonly coverage: CoverageReport;
  readonly onJobsChanged: () => Promise<void>;
  readonly onCurrentJobChange: (job: JobDetail | null) => void;
}) {
  const [step, setStep] = useState<2 | 3 | 4>(2);
  const [options, setOptions] = useState<GenerationOptions | null>(null);
  const [packReferences, setPackReferences] = useState<Awaited<
    ReturnType<typeof listPackReferences>
  >>([]);
  const [styleUploads, setStyleUploads] = useState<Awaited<
    ReturnType<typeof listUploadedReferences>
  >>([]);
  const [structureUploads, setStructureUploads] = useState<Awaited<
    ReturnType<typeof listUploadedReferences>
  >>([]);
  const [search, setSearch] = useState("");
  const [selectedTargetSemanticId, setSelectedTargetSemanticId] = useState<string | null>(
    null,
  );
  const [styleReferences, setStyleReferences] = useState<readonly ReferenceSelection[]>(
    [],
  );
  const [structureReference, setStructureReference] = useState<ReferenceSelection | null>(
    null,
  );
  const [userDescription, setUserDescription] = useState("");
  const [userNegativePrompt, setUserNegativePrompt] = useState("");
  const [resolution, setResolution] = useState<16 | 32 | 64>(16);
  const [parallelism, setParallelism] = useState<1 | 2 | 4>(1);
  const [denoise, setDenoise] = useState<number | null>(null);
  const [styleWeight, setStyleWeight] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [uploadingKind, setUploadingKind] = useState<"style" | "structure" | null>(
    null,
  );
  const [referenceError, setReferenceError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoadError(null);
    void Promise.all([
      getGenerationOptions(projectId, manifest, coverage),
      listPackReferences(projectId),
      listUploadedReferences(projectId, "style"),
      listUploadedReferences(projectId, "structure"),
    ]).then(([loadedOptions, loadedPackReferences, loadedStyleUploads, loadedStructureUploads]) => {
      if (!active) {
        return;
      }
      setOptions(loadedOptions);
      setPackReferences(loadedPackReferences);
      setStyleUploads(loadedStyleUploads);
      setStructureUploads(loadedStructureUploads);
      setResolution(loadedOptions.defaults.resolution);
      setParallelism(loadedOptions.defaults.parallelism);
    }).catch((cause: unknown) => {
      if (active) {
        setLoadError(
          cause instanceof Error ? cause.message : "生成配置读取失败",
        );
      }
    });
    return () => {
      active = false;
    };
  }, [coverage, manifest, projectId]);

  const filteredTargets = useMemo(() => {
    if (options === null) {
      return [];
    }
    const query = search.trim().toLocaleLowerCase("en-US");
    return options.targets.filter((target) => {
      if (query.length === 0) {
        return true;
      }
      return (
        target.displayName.toLocaleLowerCase("en-US").includes(query) ||
        target.semanticId.toLocaleLowerCase("en-US").includes(query) ||
        target.relativePath.toLocaleLowerCase("en-US").includes(query)
      );
    });
  }, [options, search]);

  function toggleStyleReference(selection: ReferenceSelection) {
    setStyleReferences((current) => {
      const exists = current.some((item) =>
        item.source === selection.source &&
        (item.source === "pack"
          ? selection.source === "pack" && item.relativePath === selection.relativePath
          : selection.source === "upload" &&
            item.referenceId === selection.referenceId),
      );
      if (exists) {
        return current.filter((item) =>
          !(item.source === selection.source &&
            (item.source === "pack"
              ? selection.source === "pack" &&
                item.relativePath === selection.relativePath
              : selection.source === "upload" &&
                item.referenceId === selection.referenceId)),
        );
      }
      if (current.length >= 8) {
        return current;
      }
      return [...current, selection];
    });
  }

  async function handleUpload(kind: "style" | "structure", file: File) {
    setUploadingKind(kind);
    setReferenceError(null);
    try {
      const uploaded = await uploadReference(projectId, kind, file);
      if (kind === "style") {
        setStyleUploads((current) =>
          current.some((item) => item.referenceId === uploaded.referenceId)
            ? current
            : [...current, uploaded],
        );
        setStyleReferences((current) =>
          current.length >= 8 ||
          current.some(
            (item) =>
              item.source === "upload" &&
              item.referenceId === uploaded.referenceId,
          )
            ? current
            : [...current, { source: "upload", referenceId: uploaded.referenceId }],
        );
      } else {
        setStructureUploads((current) =>
          current.some((item) => item.referenceId === uploaded.referenceId)
            ? current
            : [...current, uploaded],
        );
        setStructureReference({
          source: "upload",
          referenceId: uploaded.referenceId,
        });
      }
    } catch (cause) {
      setReferenceError(
        cause instanceof Error ? cause.message : "参考图上传失败",
      );
    } finally {
      setUploadingKind(null);
    }
  }

  async function handleDelete(kind: "style" | "structure", referenceId: string) {
    setReferenceError(null);
    try {
      await deleteUploadedReference(projectId, kind, referenceId);
      if (kind === "style") {
        setStyleUploads((current) =>
          current.filter((item) => item.referenceId !== referenceId),
        );
        setStyleReferences((current) =>
          current.filter(
            (item) =>
              item.source !== "upload" || item.referenceId !== referenceId,
          ),
        );
      } else {
        setStructureUploads((current) =>
          current.filter((item) => item.referenceId !== referenceId),
        );
        setStructureReference((current) =>
          current?.source === "upload" && current.referenceId === referenceId
            ? null
            : current,
        );
      }
    } catch (cause) {
      setReferenceError(
        cause instanceof Error ? cause.message : "参考图删除失败",
      );
    }
  }

  async function handleCreateAndStart() {
    if (selectedTargetSemanticId === null) {
      return;
    }
    setCreating(true);
    try {
      const created = await createGenerationJob(projectId, {
        targetSemanticId: selectedTargetSemanticId,
        userDescription,
        userNegativePrompt,
        resolution,
        parallelism,
        styleReferences,
        structureReference,
        denoise: structureReference === null ? null : denoise,
        styleWeight: styleReferences.length === 0 ? null : styleWeight,
      });
      const started = await startGenerationJob(projectId, created.request.jobId);
      onCurrentJobChange(started as unknown as JobDetail);
      await onJobsChanged();
    } catch {
      // The app-level caller owns error presentation in later tasks.
    } finally {
      setCreating(false);
    }
  }

  if (options === null) {
    return (
      <section className="panel generation-panel">
        <p className="loading-note">
          {loadError === null ? "正在读取生成配置…" : `生成配置读取失败：${loadError}`}
        </p>
      </section>
    );
  }

  if (step === 2) {
    return (
      <TargetStep
        search={search}
        selectedSemanticId={selectedTargetSemanticId}
        targets={filteredTargets}
        onNext={() => setStep(3)}
        onSearchChange={setSearch}
        onSelect={setSelectedTargetSemanticId}
      />
    );
  }

  if (step === 3) {
    return (
      <ReferenceStep
        packReferences={packReferences}
        selectedStructureReference={structureReference}
        selectedStyleReferences={styleReferences}
        structureUploads={structureUploads}
        styleUploads={styleUploads}
        userDescription={userDescription}
        onBack={() => setStep(2)}
        onNext={() => setStep(4)}
        onStructureReferenceChange={setStructureReference}
        onToggleStyleReference={toggleStyleReference}
        onUserDescriptionChange={setUserDescription}
        onUpload={(kind, file) => void handleUpload(kind, file)}
        onDelete={(kind, referenceId) => void handleDelete(kind, referenceId)}
        uploadingKind={uploadingKind}
        error={referenceError}
      />
    );
  }

  return (
    <GenerationStep
      creating={creating}
      denoise={denoise}
      hasStructureReference={structureReference !== null}
      hasStyleReference={styleReferences.length > 0}
      options={options}
      parallelism={parallelism}
      resolution={resolution}
      styleWeight={styleWeight}
      userNegativePrompt={userNegativePrompt}
      onBack={() => setStep(3)}
      onCreateAndStart={() => void handleCreateAndStart()}
      onDenoiseChange={setDenoise}
      onNegativePromptChange={setUserNegativePrompt}
      onParallelismChange={setParallelism}
      onResolutionChange={setResolution}
      onStyleWeightChange={setStyleWeight}
    />
  );
}
