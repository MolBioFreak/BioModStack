// Keep the controller publisher (including cached-task republication). A
// physically separate result filesystem still requires its portable body.
def ngsResultPublishMode(workDir, outputDir) {
    def destination = file(outputDir).toAbsolutePath()
    while (!java.nio.file.Files.exists(destination)) {
        destination = destination.parent
    }
    try {
        return java.nio.file.Files.getFileStore(workDir) == java.nio.file.Files.getFileStore(destination) ? 'link' : 'copy'
    } catch (java.io.IOException | UnsupportedOperationException ignored) {
        return 'copy'
    }
}
