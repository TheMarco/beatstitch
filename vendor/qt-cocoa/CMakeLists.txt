cmake_minimum_required(VERSION 3.24)
project(DJQtCocoaFix LANGUAGES CXX OBJCXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_OBJCXX_STANDARD 17)
set(CMAKE_AUTOMOC ON)
find_package(Qt6 6.11.2 EXACT REQUIRED COMPONENTS Core Gui CorePrivate GuiPrivate)

# Use the matching installed SDK for headers/moc, but link the actual app runtime.
if(NOT DJ_QT_RUNTIME)
    message(FATAL_ERROR "Pass DJ_QT_RUNTIME pointing to the verified PySide6 Qt/lib directory")
endif()
get_filename_component(runtime "${DJ_QT_RUNTIME}" ABSOLUTE)
foreach(module Core Gui)
    set_target_properties(Qt6::${module} PROPERTIES
        IMPORTED_LOCATION_RELEASE "${runtime}/Qt${module}.framework/Versions/A/Qt${module}")
endforeach()

set(cocoa "${CMAKE_CURRENT_SOURCE_DIR}/qtbase-6.11.2/src/plugins/platforms/cocoa")
# The official PySide6 macOS wheel omits Vulkan (verified against its QtGui
# exports); the Homebrew header set enables it. Match the linked runtime.
set(QT_FEATURE_vulkan OFF)
file(GLOB sources "${cocoa}/*.mm" "${cocoa}/*.cpp" "${cocoa}/*.h")
# These are included by qnsview.mm rather than independent translation units.
list(FILTER sources EXCLUDE REGEX "/qnsview_[^/]*\\.mm$")
if(NOT QT_FEATURE_vulkan)
    list(FILTER sources EXCLUDE REGEX "/qcocoavulkaninstance\\.(mm|h)$")
endif()
if(NOT QT_FEATURE_opengl)
    list(FILTER sources EXCLUDE REGEX "/qcocoaglcontext\\.(mm|h)$")
endif()
if(NOT QT_FEATURE_sessionmanager)
    list(FILTER sources EXCLUDE REGEX "/qcocoasessionmanager\\.(cpp|h)$")
endif()
qt_add_plugin(QCocoaIntegrationPlugin MODULE PLUGIN_TYPE platforms
    CLASS_NAME QCocoaIntegrationPlugin OUTPUT_NAME qcocoa)
target_sources(QCocoaIntegrationPlugin PRIVATE ${sources})
target_include_directories(QCocoaIntegrationPlugin PRIVATE "${cocoa}" "${CMAKE_CURRENT_SOURCE_DIR}/headers")
target_compile_options(QCocoaIntegrationPlugin PRIVATE
    -include "${CMAKE_CURRENT_SOURCE_DIR}/headers/QtGui/qtgui-config.h")
target_link_libraries(QCocoaIntegrationPlugin PRIVATE Qt6::CorePrivate Qt6::GuiPrivate)
foreach(framework Foundation AppKit Carbon CoreServices CoreVideo IOKit IOSurface Metal QuartzCore UniformTypeIdentifiers)
    target_link_libraries(QCocoaIntegrationPlugin PRIVATE "-framework ${framework}")
endforeach()
qt_add_resources(QCocoaIntegrationPlugin qcocoaresources
    PREFIX /qt-project.org/mac/cursors BASE "${cocoa}"
    FILES "${cocoa}/images/sizeallcursor.png" "${cocoa}/images/spincursor.png" "${cocoa}/images/waitcursor.png")
set_target_properties(QCocoaIntegrationPlugin PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/platforms"
    BUILD_RPATH "${runtime}")
