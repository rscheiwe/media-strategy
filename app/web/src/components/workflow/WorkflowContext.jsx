import { createContext, useContext } from "react";

/**
 * What workflow parts need from the chat view: which form is still open, values already
 * submitted (per form id, from persisted user-message metadata), and how to submit a form.
 */
export const WorkflowContext = createContext({
  openFormId: null,
  submitted: {},
  busy: false,
  submitForm: () => {},
});

export function useWorkflow() {
  return useContext(WorkflowContext);
}
