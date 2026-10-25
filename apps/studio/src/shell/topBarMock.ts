/** The Mock badge shows when the model profile is a mock or the project's upstream ran on one. */
export function topBarMock(profileIsMock: boolean | undefined, upstreamMock: boolean | undefined): boolean {
  return !!profileIsMock || !!upstreamMock;
}
