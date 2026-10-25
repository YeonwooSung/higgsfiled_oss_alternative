import { ProjectScreen } from "../../../../../features/pocket-vfx/components/ProjectScreen";

export default async function ProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ProjectScreen projectId={id} />;
}
