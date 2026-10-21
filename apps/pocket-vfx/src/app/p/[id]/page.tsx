import { TrimScreen } from "../../../components/TrimScreen";

export default async function ProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <TrimScreen projectId={id} />;
}
