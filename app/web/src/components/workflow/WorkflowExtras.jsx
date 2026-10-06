import { BriefDetailsForm } from "./BriefDetailsForm";
import { RecommendationCard } from "./RecommendationCard";
import { useWorkflow } from "./WorkflowContext";
import { artifactParts, formParts } from "@/lib/workflow";

/**
 * The workflow's full-width outputs for one assistant message, rendered after its activity
 * rail: the clarification form(s) and the recommendation card (latest artifact snapshot).
 */
export function WorkflowExtras({ message }) {
  const { openFormId, submitted, busy, submitForm, openArtifact } = useWorkflow();
  const forms = formParts(message);
  const artifacts = artifactParts(message);
  if (!forms.length && !artifacts.length) return null;
  return (
    <>
      {forms.map((form) => (
        <BriefDetailsForm
          key={form.form_id}
          form={form}
          interactive={form.form_id === openFormId}
          submittedValues={submitted?.[form.form_id]}
          busy={busy}
          onSubmit={submitForm}
        />
      ))}
      {artifacts.map((artifact) => (
        <RecommendationCard
          key={artifact.id}
          artifact={artifact}
          onOpen={openArtifact ? () => openArtifact(artifact.id) : undefined}
        />
      ))}
    </>
  );
}
