import { Editor } from "../../../../features/product-lock/components/Editor";

export default async function ProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <Editor projectId={id} />;
}
