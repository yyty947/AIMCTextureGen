export type GenerationJobStatus =
  | "queued"
  | "generating"
  | "postprocessing"
  | "completed"
  | "failed"
  | "canceled";

export type GenerationCandidateStatus =
  | "pending"
  | "generating"
  | "raw_ready"
  | "postprocessing"
  | "completed"
  | "failed"
  | "canceled"
  | "inherited";

export type GenerationBatchStatus =
  | "pending"
  | "generating"
  | "raw_ready"
  | "completed"
  | "failed"
  | "canceled";

export type ReferenceSelection =
  | { readonly source: "pack"; readonly relativePath: string }
  | { readonly source: "upload"; readonly referenceId: string };

export interface CreateGenerationInput {
  readonly targetSemanticId: string;
  readonly userDescription: string;
  readonly userNegativePrompt: string;
  readonly resolution: 16 | 32 | 64;
  readonly parallelism: 1 | 2 | 4;
  readonly styleReferences: readonly ReferenceSelection[];
  readonly structureReference: ReferenceSelection | null;
  readonly denoise: number | null;
  readonly styleWeight: number | null;
}

export interface GenerationTargetOption {
  readonly semanticId: string;
  readonly displayName: string;
  readonly relativePath: string;
}

export interface ResourceHint {
  readonly parallelism: 1 | 2 | 4;
  readonly peakVramMiB: number;
  readonly peakProcessRamMiB: number;
  readonly peakSystemRamMiB: number;
  readonly elapsedSeconds: number;
}

export interface GenerationOptions {
  readonly candidateCount: 4;
  readonly allowedParallelism: readonly [1, 2, 4];
  readonly defaults: {
    readonly resolution: 16 | 32 | 64;
    readonly parallelism: 1 | 2 | 4;
  };
  readonly profile: {
    readonly profileId: string;
    readonly profileVersion: string;
    readonly supportState: "candidate_unverified" | "verified";
  };
  readonly resourceHints: readonly ResourceHint[];
  readonly targets: readonly GenerationTargetOption[];
}

export interface PackReferenceRecord {
  readonly source: "pack";
  readonly relativePath: string;
  readonly displayName: string;
  readonly sha256: string;
  readonly byteSize: number;
  readonly width: number;
  readonly height: number;
  readonly mode: "RGB" | "RGBA";
}

export interface UploadedReferenceRecord {
  readonly referenceId: string;
  readonly kind: "style" | "structure";
  readonly sha256: string;
  readonly byteSize: number;
  readonly width: number;
  readonly height: number;
  readonly mode: "RGB" | "RGBA";
  readonly createdAt: string;
}

export interface FrozenReferenceRecord {
  readonly kind: "raw";
  readonly relativePath: string;
  readonly sha256: string;
  readonly byteSize: number;
  readonly mediaType: string;
  readonly width: number | null;
  readonly height: number | null;
}

export interface ExecutionBatch {
  readonly batchIndex: number;
  readonly candidateIndices: readonly (0 | 1 | 2 | 3)[];
  readonly seed: number;
}

export interface CandidateArtifactRecord {
  readonly kind: "raw" | "final" | "nearest" | "tile" | "report";
  readonly relativePath: string;
  readonly sha256: string;
  readonly byteSize: number;
  readonly mediaType: string;
  readonly width: number | null;
  readonly height: number | null;
}

export interface GenerationJobFailure {
  readonly code: string;
  readonly stage: string;
  readonly userMessage: string;
  readonly recommendedActions: readonly string[];
  readonly technicalDetails: string | null;
  readonly retryable: boolean;
  readonly occurredAt: string;
}

export interface GenerationJobRequest {
  readonly schemaVersion: 3;
  readonly jobId: string;
  readonly projectId: string;
  readonly parentJobId: string | null;
  readonly target: {
    readonly semanticId: string;
    readonly displayName: string;
    readonly relativePath: string;
    readonly catalogId: string;
  };
  readonly prompt: {
    readonly promptVersion: string;
    readonly positivePrompt: string;
    readonly negativePrompt: string;
    readonly userPrompt: string;
  };
  readonly resolution: 16 | 32 | 64;
  readonly parallelism: 1 | 2 | 4;
  readonly executionBatches: readonly ExecutionBatch[];
  readonly references: {
    readonly style: readonly FrozenReferenceRecord[];
    readonly structure: readonly FrozenReferenceRecord[];
  };
  readonly advanced: {
    readonly styleWeight: number | null;
    readonly denoise: number | null;
    readonly loraWeight: number | null;
  };
  readonly modelProfile: {
    readonly profileId: string;
    readonly profileVersion: string;
    readonly profileManifestSha256: string;
    readonly runtimeId: string;
    readonly runtimeVersion: string;
    readonly runtimeManifestSha256: string;
    readonly workflowVariant:
      | "text2img-no-style"
      | "text2img-style"
      | "img2img-no-style"
      | "img2img-style";
    readonly workflowSha256: string;
    readonly outputNodeId: string;
  };
  readonly createdAt: string;
}

export interface GenerationBatchState {
  readonly batchIndex: number;
  readonly candidateIndices: readonly (0 | 1 | 2 | 3)[];
  readonly seed: number;
  readonly status: GenerationBatchStatus;
  readonly promptId: string | null;
  readonly rawArtifacts: readonly CandidateArtifactRecord[];
  readonly startedAt: string | null;
  readonly finishedAt: string | null;
  readonly failure: GenerationJobFailure | null;
}

export interface GenerationCandidateState {
  readonly candidateIndex: 0 | 1 | 2 | 3;
  readonly batchIndex: number;
  readonly positionInBatch: number;
  readonly batchSeed: number;
  readonly status: GenerationCandidateStatus;
  readonly artifacts: {
    readonly raw: CandidateArtifactRecord | null;
    readonly final: CandidateArtifactRecord | null;
    readonly nearest: CandidateArtifactRecord | null;
    readonly tile: CandidateArtifactRecord | null;
    readonly report: CandidateArtifactRecord | null;
  };
  readonly lineage: {
    readonly parentJobId: string;
    readonly parentCandidateIndex: 0 | 1 | 2 | 3;
  } | null;
  readonly failure: GenerationJobFailure | null;
  readonly startedAt: string | null;
  readonly finishedAt: string | null;
}

export interface GenerationJobState {
  readonly schemaVersion: 2;
  readonly jobId: string;
  readonly projectId: string;
  readonly revision: number;
  readonly status: GenerationJobStatus;
  readonly cancelRequestedAt: string | null;
  readonly failure: GenerationJobFailure | null;
  readonly batches: readonly GenerationBatchState[];
  readonly candidates: readonly [
    GenerationCandidateState,
    GenerationCandidateState,
    GenerationCandidateState,
    GenerationCandidateState,
  ];
  readonly createdAt: string;
  readonly updatedAt: string;
  readonly startedAt: string | null;
  readonly finishedAt: string | null;
}

export interface GenerationJobDetail {
  readonly request: GenerationJobRequest;
  readonly state: GenerationJobState;
}
