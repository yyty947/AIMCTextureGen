import { useEffect, useMemo, useRef, useState } from "react";

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

const CREATE_FAILURE_MESSAGE = "生成任务创建失败，请检查配置后重试。";
const START_FAILURE_MESSAGE = "生成任务已创建，但启动失败，请稍后重试。";

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
  const [generationError, setGenerationError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [uploadingKind, setUploadingKind] = useState<"style" | "structure" | null>(
    null,
  );
  const [referenceError, setReferenceError] = useState<string | null>(null);
  const previousInputs = useRef({ projectId, manifest, coverage });
  const projectEpoch = useRef(0);
  const currentProjectId = useRef(projectId);
  currentProjectId.current = projectId;

  useEffect(() => {
    const previous = previousInputs.current;
    const changed =
      previous.projectId !== projectId ||
      previous.manifest !== manifest ||
      previous.coverage !== coverage;
    previousInputs.current = { projectId, manifest, coverage };
    if (!changed) {
      return;
    }

    projectEpoch.current += 1;
    setStep(2);
    setOptions(null);
    setPackReferences([]);
    setStyleUploads([]);
    setStructureUploads([]);
    setSearch("");
    setSelectedTargetSemanticId(null);
    setStyleReferences([]);
    setStructureReference(null);
    setUserDescription("");
    setUserNegativePrompt("");
    setResolution(16);
    setParallelism(1);
    setDenoise(null);
    setStyleWeight(null);
    setCreating(false);
    setGenerationError(null);
    setLoadError(null);
    setUploadingKind(null);
    setReferenceError(null);
    onCurrentJobChange(null);
  }, [coverage, manifest, onCurrentJobChange, projectId]);

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
    const operationEpoch = projectEpoch.current;
    const operationProjectId = projectId;
    const isCurrentOperation = () =>
      projectEpoch.current === operationEpoch &&
      currentProjectId.current === operationProjectId;
    setUploadingKind(kind);
    setReferenceError(null);
    try {
      const uploaded = await uploadReference(projectId, kind, file);
      if (!isCurrentOperation()) {
        return;
      }
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
      if (!isCurrentOperation()) {
        return;
      }
      setReferenceError(
        cause instanceof Error ? cause.message : "参考图上传失败",
      );
    } finally {
      if (isCurrentOperation()) {
        setUploadingKind(null);
      }
    }
  }

  async function handleDelete(kind: "style" | "structure", referenceId: string) {
    const operationEpoch = projectEpoch.current;
    const operationProjectId = projectId;
    const isCurrentOperation = () =>
      projectEpoch.current === operationEpoch &&
      currentProjectId.current === operationProjectId;
    setReferenceError(null);
    try {
      await deleteUploadedReference(projectId, kind, referenceId);
      if (!isCurrentOperation()) {
        return;
      }
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
      if (!isCurrentOperation()) {
        return;
      }
      setReferenceError(
        cause instanceof Error ? cause.message : "参考图删除失败",
      );
    }
  }

  async function handleCreateAndStart() {
    if (selectedTargetSemanticId === null) {
      return;
    }
    const requestEpoch = projectEpoch.current;
    setCreating(true);
    setGenerationError(null);
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
      if (requestEpoch !== projectEpoch.current) {
        return;
      }
      onCurrentJobChange(created as unknown as JobDetail);
      try {
        const started = await startGenerationJob(projectId, created.request.jobId);
        if (requestEpoch !== projectEpoch.current) {
          return;
        }
        onCurrentJobChange(started as unknown as JobDetail);
        await onJobsChanged();
      } catch {
        if (requestEpoch !== projectEpoch.current) {
          return;
        }
        try {
          await onJobsChanged();
        } catch {
          // Keep the stable start error visible even if refreshing history fails.
        }
        setGenerationError(START_FAILURE_MESSAGE);
      }
    } catch {
      if (requestEpoch === projectEpoch.current) {
        setGenerationError(CREATE_FAILURE_MESSAGE);
      }
    } finally {
      if (requestEpoch === projectEpoch.current) {
        setCreating(false);
      }
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
      error={generationError}
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
