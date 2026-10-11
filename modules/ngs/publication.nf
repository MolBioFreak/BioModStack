// Keep the controller publisher (including cached-task republication). A
// physically separate result filesystem still requires its portable body.
def ngsResultPublishMode(workDir, outputDir) {
    def destination = file(outputDir).toAbsolutePath()
    while (!java.nio.file.Files.exists(destination)) {
        destination = destination.parent
    }
    java.nio.file.Files.getFileStore(workDir) == java.nio.file.Files.getFileStore(destination) ? 'link' : 'copy'
}
